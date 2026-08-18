"""Session 11 — incremental corpus expansion + index status.

Three endpoints under ``/embeddings/index``:

* ``POST /runs``       — add a batch of new documents to the corpus. Records a
  job row, dispatches the expansion as an async BackgroundTask, returns 202
  immediately with the ``job_id`` (mirrors ``api/ingestion.py``).
* ``GET  /jobs/{id}``  — poll the job's progress (documents processed / status).
* ``GET  /stats``      — per-collection corpus size + whether it is HNSW-indexed,
  so the UI can show the corpus growing.

The batch is embedded with the SAME model as the corpus and lands in
``budget_chunks``, where the Session 11 HNSW index picks it up automatically. The
BackgroundTask is ASYNC (the expansion uses the async pgvector engine); the job
row is updated through the sync ``JobsRepository`` off-loaded to a thread so the
async engine and the sync session never share a loop.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.dependencies import get_chunk_store, get_corpus_index_service
from app.foundation.persistence.database import (
    SessionLocal,
    get_async_session_factory,
    get_session,
)
from app.foundation.persistence.repositories.jobs import JobsRepository
from app.generation.rag.index_service import CorpusIndexService
from app.generation.rag.schemas import (
    Budget,
    CollectionStats,
    CorpusStats,
    IndexJobView,
    IndexRunRequest,
    IndexRunResponse,
)
from app.generation.rag.store.repository import ChunkStore

log = structlog.get_logger()

router = APIRouter(prefix="/embeddings", tags=["corpus-index"])


def _job_update(fn: Callable[[JobsRepository], None]) -> None:
    """Run one job mutation on its own short-lived sync session (thread-safe)."""
    session = SessionLocal()
    try:
        fn(JobsRepository(session))
    finally:
        session.close()


async def _run_expansion(
    *,
    job_id: uuid.UUID,
    documents: list[Budget],
    document_type: str,
    chunk_type: str,
    service: CorpusIndexService,
) -> None:
    """Async BackgroundTask body: expand the corpus, updating the job as it goes.

    The expansion awaits the async pgvector engine; the job row is a separate
    sync store, updated with brief blocking writes (fine at teaching scale, and
    it keeps the async engine and the sync session off each other's loop)."""
    _job_update(lambda repo: repo.mark_running(job_id))
    try:
        result = await service.expand(
            documents,
            document_type=document_type,
            chunk_type=chunk_type,
            on_progress=lambda n: _job_update(lambda repo: repo.set_documents_count(job_id, n)),
        )
        _job_update(
            lambda repo: repo.mark_completed(job_id, documents_count=result.documents_indexed)
        )
    except Exception as exc:  # noqa: BLE001 — record the failure, log loudly.
        message = str(exc)
        log.error("corpus_expansion_failed", job_id=str(job_id), error=message[:400])
        _job_update(lambda repo: repo.mark_failed(job_id, error_message=message))


@router.post("/index/runs", response_model=IndexRunResponse, status_code=202)
def create_index_run(
    request: IndexRunRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    service: CorpusIndexService | None = Depends(get_corpus_index_service),
) -> IndexRunResponse | JSONResponse:
    """Add a batch of new documents to the corpus (async). Returns 202 + job_id."""
    if service is None:
        log.error("corpus_index_unavailable", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    job = JobsRepository(session).create(source_name=f"corpus-expansion:{request.chunk_type}")
    background.add_task(
        _run_expansion,
        job_id=job.job_id,
        documents=request.documents,
        document_type=request.document_type,
        chunk_type=request.chunk_type,
        service=service,
    )
    log.info(
        "corpus_index_run_created",
        job_id=str(job.job_id),
        documents_total=len(request.documents),
    )
    return IndexRunResponse(
        job_id=job.job_id, documents_total=len(request.documents), status=job.status
    )


@router.get("/index/jobs/{job_id}", response_model=IndexJobView)
def get_index_job(
    job_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> IndexJobView:
    """Poll a corpus-expansion job's progress."""
    job = JobsRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return IndexJobView(
        job_id=job.job_id,
        status=job.status,
        documents_processed=job.documents_count,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/index/stats", response_model=CorpusStats)
async def get_corpus_stats(
    store: ChunkStore = Depends(get_chunk_store),
) -> CorpusStats:
    """Per-collection corpus size + HNSW index state (the growth panel)."""
    factory = get_async_session_factory()
    async with factory() as session:
        rows = await store.corpus_stats(session)
    collections = [
        CollectionStats(collection=name, documents=docs, chunks=chunks, hnsw_indexed=indexed)
        for (name, docs, chunks, indexed) in rows
    ]
    return CorpusStats(
        collections=collections, total_chunks=sum(c.chunks for c in collections)
    )
