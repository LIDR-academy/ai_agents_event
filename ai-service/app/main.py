import structlog
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.api.embeddings import router as embeddings_router
from app.api.search import router as search_router
from app.api import config as config_api
from app.api import estimations, ingestion, sessions
from app.api.rate_limiting import limiter, rate_limit_exceeded_handler
from app.api.service_token import service_token_middleware
from app.api.routers.estimate import router as estimate_router
from app.api.routers.estimate_agent import router as estimate_agent_router
from app.api.routers.estimate_stages import router as estimate_stages_router
from app.api.routers.estimate_tasks import router as estimate_tasks_router
from app.api.routers.corpus_index import router as corpus_index_router
from app.api.routers.estimate_graph import router as estimate_graph_router
from app.api.routers.estimate_supervisor import router as estimate_supervisor_router
from app.api.routers.retrieval import router as retrieval_router
from app.api.routers.retrieval_advanced import router as retrieval_advanced_router


def configure_logging() -> None:
    """Set up structlog: JSON in production, human-readable in development."""
    settings = get_settings()

    if settings.APP_ENV == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    configure_logging()
    log = structlog.get_logger()
    settings = get_settings()
    # Session 6: fail fast on a malformed catalog rather than at the first
    # ingestion request. Catalogs are versioned in git; a broken one is a
    # deploy-time problem, not a request-time one.
    try:
        from app.dependencies import get_catalog

        catalog = get_catalog()
        log.info(
            "catalog_loaded",
            version=catalog.version,
            sources_total=len(catalog.sources),
            sources_included=len(catalog.included_sources()),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("catalog_load_failed", error=str(exc)[:400])

    # Session 13: build the estimation graph with a Postgres checkpointer over the
    # project database (its tables coexist with pgvector). Held open for the app's
    # lifetime via an AsyncExitStack; a failure here (e.g. Postgres unreachable)
    # leaves app.state.graph = None so the graph endpoint 503s WITHOUT taking down
    # the unrelated routers.
    app.state.graph = None
    app.state.supervisor_graph = None
    app.state._graph_stack = AsyncExitStack()

    checkpointer = None
    try:
        from app.domain.graph.checkpointer import open_checkpointer

        checkpointer = await app.state._graph_stack.enter_async_context(open_checkpointer())
    except Exception as exc:  # noqa: BLE001 — the graphs are optional infrastructure.
        log.error("graph_checkpointer_init_failed", error=str(exc)[:400])

    if checkpointer is not None:
        # The two graphs are built independently so one failing cannot take the other
        # down; they SHARE the checkpointer (one pool, one set of tables) and the
        # routers namespace their thread ids so the states never collide.
        try:
            from app.domain.graph.build import build_graph

            app.state.graph = build_graph(checkpointer)
            log.info("graph_ready")
        except Exception as exc:  # noqa: BLE001
            log.error("graph_init_failed", error=str(exc)[:400])

        # Session 14: the SUPERVISOR graph — a hand-built router over four
        # least-privilege agents, with a confidence-triggered human gate. The S14-live
        # competition + sandboxing variants are toggled by settings (off by default);
        # ``build_supervisor_graph`` also runs ``verify_tool_grants`` and would fail the
        # startup of THIS graph — and only this graph — on an inconsistent grant table.
        try:
            from app.domain.graph.supervisor.build import build_supervisor_graph

            app.state.supervisor_graph = build_supervisor_graph(
                checkpointer,
                competitive=settings.SUPERVISOR_COMPETITION_ENABLED,
                sandboxed=settings.SUPERVISOR_PERSISTENCE_ENABLED,
            )
            log.info(
                "supervisor_graph_ready",
                competitive=settings.SUPERVISOR_COMPETITION_ENABLED,
                sandboxed=settings.SUPERVISOR_PERSISTENCE_ENABLED,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("supervisor_graph_init_failed", error=str(exc)[:400])

    log.info("application_started", environment=settings.APP_ENV)
    yield
    await app.state._graph_stack.aclose()
    log.info("application_shutdown")


app = FastAPI(
    title="Software Estimation Service",
    description="AI-powered software estimation service with typed input and versioned prompts",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Session 13: configure Logfire and instrument FastAPI + httpx so a graph run emits
# one span per node inside the request trace. No-op without a LOGFIRE_TOKEN — never
# breaks startup (see app/domain/graph/observability.py).
from app.domain.graph.observability import configure_logfire  # noqa: E402

configure_logfire(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session 15: shared-secret gate in front of the whole application.
#
# Ordering matters and is not obvious: Starlette runs middleware in REVERSE
# registration order, so whatever is added last sits outermost. Registering the
# token check here — after CORS, before request_id_middleware below — yields
#
#     request_id  →  service_token  →  CORS  →  routers
#
# which is what we want: a correlation id is bound before the rejection is
# logged, and the 401 short-circuits before any router or dependency runs.
#
# No-op while AI_SERVICE_TOKEN is unset (tests, local uvicorn). See
# app/api/service_token.py for the full rationale.
app.middleware("http")(service_token_middleware)

# Session 9: per-API-key rate limiting (slowapi). The decorators on the routers
# read ``app.state.limiter``; a custom handler turns the limit breach into a
# JSON 429 with a ``Retry-After`` header.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Assign a correlation id per request, bind it for structlog, and echo it
    back on the ``X-Request-ID`` response header so failures are debuggable."""
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(estimations.router)
app.include_router(sessions.router)
app.include_router(ingestion.router)
app.include_router(embeddings_router)
app.include_router(corpus_index_router)
app.include_router(search_router)
app.include_router(config_api.router)
# Session 9 — RAG retrieval + grounded estimation (each independently secured).
app.include_router(retrieval_router)
# Session 10 — advanced multi-index retrieval (routing, expansion, decay).
app.include_router(retrieval_advanced_router)
app.include_router(estimate_router)
# Per-stage endpoints exposing each pipeline step (wizard / live-session aid).
app.include_router(estimate_stages_router)
# Session 10 — per-task hours estimation by vector search (structure → hours).
app.include_router(estimate_tasks_router)
# Session 12 — hand-written agent over the budget retrieval (decision layer).
app.include_router(estimate_agent_router)
# Session 13 — the estimation flow as an explicit LangGraph StateGraph.
app.include_router(estimate_graph_router)
# Session 14: the estimation flow as a supervisor + four least-privilege agents.
app.include_router(estimate_supervisor_router)


@app.get("/health")
async def health_check() -> dict:
    """Return service health status."""
    settings = get_settings()
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": settings.APP_ENV,
    }
