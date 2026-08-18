"""Per-task hours estimation by vector search (Session 10).

The structure-only generation produces modules → tasks WITHOUT hours. This
module derives the hours for each task from the historical task corpus
(``chunk_type='historical_task'``, built by ``scripts/build_task_corpus.py``):

1. compose a search text from the task (module + name + description),
2. embed it and run a metadata-filtered k-NN over the historical tasks,
3. combine the nearest neighbours' recorded ``estimated_hours`` into a single
   number by a DISTANCE-WEIGHTED CONSENSUS, with a reliability score that blends
   how close the neighbours are with how much they agree.

A task whose nearest neighbour is farther than the distance threshold gets NO
hours (``has_match=False``) — surfaced as a red flag in the UI, where the human
supplies the number. The retriever does no numeric aggregation beyond returning
the chunks; the consensus lives here as a pure, testable function.

RAG-only: reuses the embedder and ``search_chunks`` (Session 9) — it does not
import any other ``generation`` sibling, per ARCHITECTURE.md.
"""

from __future__ import annotations

import asyncio
import statistics

import structlog

from app.config import get_settings
from app.generation.rag.retrieval.collections import Collection
from app.generation.rag.retrieval.pipeline import retrieve
from app.generation.rag.schemas import (
    RetrievedChunk,
    TaskHoursEstimate,
    TaskHoursModuleInput,
    TaskHoursResult,
    TaskNeighbor,
)
from app.generation.rag.quality.synthesis import synthesize_range

log = structlog.get_logger()

_HISTORICAL_TASK_CHUNK_TYPE = "historical_task"
# Floor on the inverse-distance weight so an exact match (distance ≈ 0) does not
# blow up to an infinite weight and silence every other neighbour.
_WEIGHT_EPS = 1e-3


def compose_task_search_text(module: str, name: str, description: str | None) -> str:
    """Build the embeddable query text for one task.

    Mirrors the shape of the historical-task chunk text (module + component
    name + description) so query and corpus live in the same vocabulary space.
    """
    parts = []
    if module:
        parts.append(f"Module: {module}")
    parts.append(f"Task: {name}")
    if description:
        parts.append(description)
    return "\n".join(parts)


def _consensus(neighbors: list[tuple[int, float]]) -> tuple[int, float, float]:
    """Distance-weighted consensus over ``(hours, distance)`` neighbours.

    Returns ``(hours, reliability, dispersion)``:

    * ``hours`` — inverse-distance weighted mean of the neighbour hours, rounded.
    * ``reliability`` — ``weighted_similarity * (1 - min(dispersion, 1))`` in
      ``[0, 1]``: high only when the neighbours are both CLOSE and in AGREEMENT.
    * ``dispersion`` — coefficient of variation of the neighbour hours (std/mean),
      a transparent "how much do they disagree" number.

    Precondition: ``neighbors`` is non-empty (the caller handles the no-match
    case before calling this).
    """
    weights = [1.0 / (_WEIGHT_EPS + dist) for _hours, dist in neighbors]
    total_w = sum(weights)
    hours_values = [hours for hours, _dist in neighbors]

    weighted_hours = sum(w * h for w, (h, _d) in zip(weights, neighbors)) / total_w

    # Weighted mean of per-neighbour similarity (1 - cosine distance, floored at 0).
    weighted_similarity = (
        sum(w * max(0.0, 1.0 - dist) for w, (_h, dist) in zip(weights, neighbors)) / total_w
    )

    mean_hours = statistics.fmean(hours_values)
    if len(hours_values) > 1 and mean_hours > 0:
        dispersion = statistics.pstdev(hours_values) / mean_hours
    else:
        dispersion = 0.0

    reliability = weighted_similarity * (1.0 - min(dispersion, 1.0))
    reliability = max(0.0, min(1.0, reliability))
    return round(weighted_hours), round(reliability, 3), round(dispersion, 3)


# Public alias for the consensus primitive. Session 12: the agentic hours-recovery
# loop injects this as its ``consensus_fn`` (via the conductor) so the agent's
# ``derive_task_hours`` tool reuses the SAME distance-weighted math as the
# deterministic path — the agent decides the search, never the arithmetic.
distance_weighted_consensus = _consensus


async def estimate_one(
    module: str,
    name: str,
    description: str | None,
    *,
    top_k: int,
    distance_threshold: float,
    search_mode: str = "vector",
    rerank: bool = False,
    synthesis: bool = False,
    contradiction_threshold: float = 0.35,
) -> TaskHoursEstimate:
    """Estimate hours for a single task from the historical task corpus.

    The per-task search goes through the full retrieval pipeline (hybrid + optional
    cross-encoder reranking), filtered to ``historical_task`` chunks, so reranking
    applies here when enabled — the neighbours that feed the consensus are the ones
    the cross-encoder judges most relevant, not just the closest in cosine space.
    """
    from app.dependencies import get_embedder

    settings = get_settings()
    embedder = get_embedder()
    if embedder is None:
        raise RuntimeError("Embedding service is not available (no OpenAI key).")

    search_text = compose_task_search_text(module, name, description)
    embedding = await asyncio.to_thread(embedder.embed_one, search_text)
    result = await retrieve(
        query_embedding=embedding,
        query_text=search_text,
        search_mode=search_mode,
        rerank=rerank,
        top_k=top_k,
        recall_k=settings.RETRIEVAL_RECALL_TOP_K,
        rerank_top_n=top_k,
        distance_threshold=distance_threshold,
        rrf_k=settings.RRF_K,
        collection=Collection.BUDGET,
        chunk_types=[_HISTORICAL_TASK_CHUNK_TYPE],
    )

    usable: list[RetrievedChunk] = [c for c in result.chunks if c.estimated_hours is not None]
    if not usable:
        return TaskHoursEstimate(module=module, task=name, has_match=False)

    neighbors = [
        TaskNeighbor(
            source_id=c.id,
            budget_id=c.budget_id,
            estimated_hours=int(c.estimated_hours),
            distance=c.distance,
        )
        for c in usable
    ]
    hours, reliability, dispersion = _consensus(
        [(n.estimated_hours, n.distance) for n in neighbors]
    )
    # Session 11: when the neighbours contradict beyond the threshold, surface the
    # spread as a range with a reason instead of averaging the conflict away. The
    # point ``estimated_hours`` still carries the consensus for the human to edit.
    hours_range = (
        await synthesize_range(neighbors, dispersion, threshold=contradiction_threshold)
        if synthesis
        else None
    )
    return TaskHoursEstimate(
        module=module,
        task=name,
        estimated_hours=hours,
        reliability=reliability,
        has_match=True,
        dispersion=dispersion,
        neighbors=neighbors,
        hours_range=hours_range,
    )


async def estimate_all(
    modules: list[TaskHoursModuleInput],
    *,
    top_k: int | None = None,
    distance_threshold: float | None = None,
) -> TaskHoursResult:
    """Estimate hours for every task across all modules, concurrently.

    One vector search per task (fanned out with ``asyncio.gather``); the order of
    the returned estimates matches the order the tasks were submitted.
    """
    from app.dependencies import get_runtime_retrieval_config

    settings = get_settings()
    k = top_k if top_k is not None else settings.TASK_HOURS_TOP_K
    threshold = (
        distance_threshold
        if distance_threshold is not None
        else settings.TASK_HOURS_DISTANCE_THRESHOLD
    )
    # Search mode + reranking follow the runtime/settings defaults (Ajustes UI), so
    # the per-task search benefits from hybrid/rerank without changing this contract.
    runtime = get_runtime_retrieval_config()
    search_mode = runtime.effective_search_mode()
    rerank = runtime.effective_rerank()
    synthesis = runtime.effective_synthesis()

    coros = [
        estimate_one(
            module.name,
            task.name,
            task.description,
            top_k=k,
            distance_threshold=threshold,
            search_mode=search_mode,
            rerank=rerank,
            synthesis=synthesis,
            contradiction_threshold=settings.SYNTHESIS_CONTRADICTION_THRESHOLD,
        )
        for module in modules
        for task in module.tasks
    ]
    estimates = await asyncio.gather(*coros)
    matched = sum(1 for e in estimates if e.has_match)
    log.info(
        "task_hours_done",
        tasks=len(estimates),
        matched=matched,
        flagged=len(estimates) - matched,
        top_k=k,
        distance_threshold=threshold,
        search_mode=search_mode,
        rerank=rerank,
    )
    return TaskHoursResult(tasks=list(estimates))
