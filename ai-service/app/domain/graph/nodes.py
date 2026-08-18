"""The five graph nodes (Level 1) — pure ``state -> partial update`` functions.

Each node reuses the Session 9–12 building blocks rather than re-implementing them:

* ``extract_requirements`` / ``classify_components`` / ``generate_estimate`` go
  through ``LLMWrapper.complete_structured`` (Instructor + LiteLLM) — the same
  structured-output path the RAG reformulator uses.
* ``search_budgets`` reuses the real S9/S10 retrieval via
  ``make_retrieval_backend`` (hybrid search + optional reranking over the budget
  collection, ``chunk_type='historical_task'``).
* ``validate_and_consolidate`` ports the deterministic guardrails from
  ``agent_tools.validate_estimate``.

Observability (Level 2): every node body runs inside ``with logfire.span("node:
<name>")`` so a run produces one span per node. Logfire is a no-op when no token
is configured (``observability.configure_logfire``), so the spans never break a
run — they just do not export.

Dependency wiring: nodes self-wire through local ``from app.dependencies import
...`` imports (the tolerated composition-root touch, matching
``app/generation/rag/agent_retrieval.py``). Tests monkeypatch these module-level
symbols to run the graph network-free.
"""

from __future__ import annotations

import asyncio

import logfire
import structlog

from app.config import get_settings
from app.domain.graph.schemas import (
    ComponentClassification,
    ConsolidatedEstimate,
    RequirementsExtraction,
)
from app.domain.graph.state import BudgetMatch, Component, EstimationState
from app.generation.rag.agent_retrieval import make_retrieval_backend

log = structlog.get_logger()

# References are historical engineer-HOURS; the estimate is in engineer-DAYS. One
# working day = 8 hours, used to bring both onto the same axis for validation.
HOURS_PER_DAY = 8.0

_EXTRACT_SYSTEM_PROMPT = (
    "You are a software-delivery analyst. Read a raw, messy client meeting "
    "transcript and extract a flat list of the concrete requirements the client "
    "wants built. One atomic requirement per item, concise technical English, "
    "regardless of the transcript language. Ignore small talk, anecdotes and "
    "digressions. Never invent requirements the transcript gives no evidence for."
)

_CLASSIFY_SYSTEM_PROMPT = (
    "You are a solution architect. Group a list of project requirements into the "
    "distinct functional COMPONENTS needed to deliver them. Each component has a "
    "short name and a coarse category (e.g. backend, integration, mobile, "
    "analytics, frontend, infrastructure). Merge requirements that belong to the "
    "same component; keep genuinely unrelated pieces separate."
)

_GENERATE_SYSTEM_PROMPT = (
    "You are a senior estimator. Consolidate a set of components and the historical "
    "reference budgets retrieved for each (recorded in engineer-HOURS) into a single "
    "structured estimate expressed in engineer-DAYS.\n"
    "Method for each component:\n"
    "1. Convert every reference from hours to engineer-days by DIVIDING by 8 "
    "(8 working hours per day).\n"
    "2. Put the ROUNDED MEDIAN of those per-reference day values in the component's "
    "`engineer_days` field as an integer — the field itself, not just the rationale. "
    "That keeps it anchored in the historical range, not a figure you invent.\n"
    "3. If a component has NO references, set its `engineer_days` to null and say so "
    "in its rationale.\n"
    "Finally, set total_engineer_days to the exact SUM of the grounded components' "
    "engineer_days (treat null as 0 in the sum)."
)


def _is_reasoning_model(model: str) -> bool:
    """gpt-5 family are reasoning models (need an effort + a big token budget)."""
    return model.lower().startswith("gpt-5")


def _reasoning_effort_for(model: str) -> str | None:
    """Reasoning models (gpt-5 family) accept an effort; others must not get one."""
    return "low" if _is_reasoning_model(model) else None


def _max_tokens_for(model: str) -> int:
    """Token ceiling per model.

    A reasoning model spends tokens on hidden reasoning before the JSON, so it needs
    the generous ``GENERATION_MAX_TOKENS`` budget. A plain chat model must NOT get
    that value — it exceeds e.g. gpt-4o-mini's output cap and the call errors — so it
    falls back to the wrapper's default, which is plenty for the small estimate.
    """
    return get_settings().GENERATION_MAX_TOKENS if _is_reasoning_model(model) else 4000


async def extract_requirements(state: EstimationState) -> dict:
    """Transcript → a flat list of requirements (structured LLM call)."""
    with logfire.span("node: extract_requirements"):
        settings = get_settings()
        from app.dependencies import get_llm_wrapper

        wrapper = get_llm_wrapper()
        result, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_EXTRACT_SYSTEM_PROMPT,
            user_message=state["transcript"],
            response_model=RequirementsExtraction,
            model_override=settings.GRAPH_EXTRACTION_MODEL,
        )
        requirements = [r.strip() for r in result.requirements if r.strip()]
        log.info("graph_node_extract_requirements", requirements=len(requirements))
        return {"requirements": requirements}


async def classify_components(state: EstimationState) -> dict:
    """Requirements → components with a category (structured LLM call)."""
    with logfire.span("node: classify_components"):
        settings = get_settings()
        from app.dependencies import get_llm_wrapper

        wrapper = get_llm_wrapper()
        requirements = state.get("requirements") or []
        user_message = "Requirements:\n" + "\n".join(f"- {r}" for r in requirements)
        result, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_CLASSIFY_SYSTEM_PROMPT,
            user_message=user_message,
            response_model=ComponentClassification,
            model_override=settings.GRAPH_EXTRACTION_MODEL,
        )
        components: list[Component] = [
            {"name": c.name.strip(), "category": c.category.strip()}
            for c in result.components
            if c.name.strip()
        ]
        log.info("graph_node_classify_components", components=len(components))
        return {"components": components}


async def search_budgets(state: EstimationState) -> dict:
    """For each component, retrieve reference budgets (SEQUENTIAL for now).

    Reuses the real S9/S10 retrieval backend. Returns only the NEW matches; the
    ``operator.add`` reducer on ``budget_matches`` appends them to the accumulator.
    A per-component retrieval failure is soft: it appends to ``errors`` and the loop
    continues (the live session upgrades this to real error handling + parallelism).
    """
    with logfire.span("node: search_budgets"):
        settings = get_settings()
        backend = make_retrieval_backend(
            top_k=settings.AGENT_SEARCH_TOP_K,
            distance_threshold=settings.AGENT_SEARCH_DISTANCE_THRESHOLD,
        )
        components = state.get("components") or []
        matches: list[BudgetMatch] = []
        errors: list[str] = []
        for component in components:
            query = f"{component['name']} ({component['category']})"
            try:
                items = await backend(query, None)
            except Exception as exc:  # noqa: BLE001 — soft-fail one component.
                log.warning(
                    "graph_node_search_budgets_error",
                    component=component["name"],
                    error=str(exc)[:200],
                )
                errors.append(f"search_budgets failed for {component['name']!r}: {exc}")
                continue
            for item in items:
                hours = item.get("estimated_hours")
                if hours is None:
                    continue
                matches.append(
                    {
                        "component": component["name"],
                        "reference_budget_id": item.get("budget_id"),
                        "amount": float(hours),
                        "distance": float(item.get("distance", 0.0)),
                    }
                )
        log.info(
            "graph_node_search_budgets",
            components=len(components),
            matches=len(matches),
            errors=len(errors),
        )
        update: dict = {"budget_matches": matches}
        if errors:
            update["errors"] = errors
        return update


def _norm(name: str) -> str:
    """Normalise a component name for matching.

    The estimate node's component names come from the LLM, which may echo the
    ``[category]`` suffix or re-case the name; the ``budget_matches`` names come
    from the classify node verbatim. Strip a trailing ``[...]``, lowercase and trim
    so a reference lookup survives that drift.
    """
    base = name.split("[", 1)[0]
    return base.strip().lower()


def _references_for(component: str, matches: list[BudgetMatch]) -> list[float]:
    """The historical reference hours retrieved for one component (name-normalised)."""
    target = _norm(component)
    return [m["amount"] for m in matches if _norm(m["component"]) == target]


async def generate_estimate(state: EstimationState) -> dict:
    """Consolidate components + their budget matches into a structured estimate."""
    with logfire.span("node: generate_estimate"):
        settings = get_settings()
        from app.dependencies import get_llm_wrapper

        wrapper = get_llm_wrapper()
        components = state.get("components") or []
        matches = state.get("budget_matches") or []

        lines: list[str] = []
        for component in components:
            refs = _references_for(component["name"], matches)
            ref_text = ", ".join(f"{h:.0f}h" for h in refs) if refs else "no references"
            lines.append(
                f"- {component['name']} [{component['category']}]: references = {ref_text}"
            )
        user_message = "Components and their historical reference budgets:\n" + "\n".join(lines)

        model = settings.GRAPH_GENERATION_MODEL
        result, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_GENERATE_SYSTEM_PROMPT,
            user_message=user_message,
            response_model=ConsolidatedEstimate,
            model_override=model,
            max_tokens=_max_tokens_for(model),
            reasoning_effort=_reasoning_effort_for(model),
        )
        log.info(
            "graph_node_generate_estimate",
            components=len(result.components),
            total_engineer_days=result.total_engineer_days,
            confidence=result.confidence,
        )
        return {"estimate": result.model_dump()}


def _validate(estimate: dict, matches: list[BudgetMatch]) -> list[str]:
    """Deterministic guardrails ported from ``agent_tools.validate_estimate``.

    Compares each component's engineer-days against the plausible range implied by
    its historical references (converted hours→days), and sanity-checks the total.
    Returns the list of issues (empty == clean).
    """
    issues: list[str] = []
    components = estimate.get("components") or []
    component_sum = 0.0
    for component in components:
        name = component.get("name", "?")
        days = component.get("engineer_days")
        refs_hours = _references_for(name, matches)
        if days is None:
            if not refs_hours:
                issues.append(f"{name!r} has no historical reference (unbudgeted).")
            continue
        component_sum += days
        if not refs_hours:
            issues.append(f"{name!r} has no historical reference (unbudgeted).")
            continue
        refs_days = [h / HOURS_PER_DAY for h in refs_hours]
        low = min(refs_days) * 0.5
        high = max(refs_days) * 2.0
        if not (low <= days <= high):
            issues.append(
                f"{name!r} estimate {days}d is outside the plausible range "
                f"[{round(low, 1)}, {round(high, 1)}]d implied by its references."
            )

    total = estimate.get("total_engineer_days")
    if total is None:
        issues.append("Total engineer-days is missing.")
    else:
        if total <= 0:
            issues.append("Total engineer-days is non-positive.")
        if abs(component_sum - total) > 0.5:
            issues.append(
                f"Total {total}d does not match the sum of components ({round(component_sum, 1)}d)."
            )
    return issues


async def validate_and_consolidate(state: EstimationState) -> dict:
    """Run guardrails over the estimate and fix the output ``status``.

    ``status`` is ``"validated"`` when clean, ``"needs_review"`` otherwise. Any
    issue is appended to the ``errors`` accumulator so the caller can surface them.
    """
    with logfire.span("node: validate_and_consolidate"):
        estimate = state.get("estimate") or {}
        matches = state.get("budget_matches") or []
        issues = _validate(estimate, matches)
        status = "validated" if not issues else "needs_review"
        log.info("graph_node_validate", status=status, issues=len(issues))
        update: dict = {"status": status}
        if issues:
            update["errors"] = issues
        return update
