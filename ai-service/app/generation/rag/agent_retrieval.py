"""Retrieval backend for the Session 12 agent's ``search_budgets`` tool.

Lives in ``rag`` (not ``agentic``) on purpose: it WRAPS the Session 9/10
``retrieve()`` pipeline over the budget collection, which is rag's territory.
The agentic loop only holds a structural callable type
(``Callable[[str, list[str] | None], Awaitable[list[dict]]]``) and receives one of
these closures INJECTED by the conductor — so neither ``rag`` imports ``agentic``
nor ``agentic`` imports ``rag`` (ARCHITECTURE.md forbids sibling imports). The
backend takes plain ``(query, sectors)`` rather than the agentic ``SearchBudgetsArgs``
precisely to keep that coupling out.

Like ``task_hours.estimate_one``, it self-wires ``get_embedder`` via a local
import (the tolerated composition-root touch), not a module-level dependency.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import structlog

from app.config import get_settings
from app.generation.rag.retrieval.collections import Collection
from app.generation.rag.retrieval.pipeline import retrieve

log = structlog.get_logger()

CONTENT_PREVIEW_CHARS = 160

# A retrieval backend is an async callable ``(query, sectors) -> list[dict]``. The
# default wraps retrieve(); a student stub can swap in a canned one for offline
# loop debugging. Kept structural (plain params) so agentic never imports rag.
RetrievalBackend = Callable[[str, list[str] | None], Awaitable[list[dict[str, Any]]]]


def make_retrieval_backend(
    top_k: int | None = None,
    distance_threshold: float | None = None,
) -> RetrievalBackend:
    """Build a retrieval backend over the real Session 9/10 hybrid pipeline.

    ``top_k`` / ``distance_threshold`` fall back to ``AGENT_SEARCH_TOP_K`` /
    ``AGENT_SEARCH_DISTANCE_THRESHOLD`` when ``None``, so a caller (e.g. an agent
    profile carried by the HTTP endpoint) can tune the search per run without
    touching global settings. ``default_retrieval_backend`` is this factory with
    both defaults.

    The returned closure embeds the query with the same model used at ingest
    time, then runs the single-collection ``retrieve()`` over the budget
    collection, restricted to ``historical_task`` chunks (those carry the recorded
    engineer-hours the agent needs). Filtering, ranking and reranking all happen
    inside ``retrieve()`` — the closure only adapts the query in and the chunks out.
    """

    async def _backend(query: str, sectors: list[str] | None) -> list[dict[str, Any]]:
        from app.dependencies import get_embedder

        embedder = get_embedder()
        if embedder is None:
            raise RuntimeError("Embedding service is not available (no OPENAI_API_KEY).")

        settings = get_settings()
        query_embedding = await asyncio.to_thread(embedder.embed_one, query)
        result = await retrieve(
            query_embedding=query_embedding,
            query_text=query,
            collection=Collection.BUDGET,
            chunk_types=["historical_task"],
            top_k=top_k if top_k is not None else settings.AGENT_SEARCH_TOP_K,
            distance_threshold=(
                distance_threshold
                if distance_threshold is not None
                else settings.AGENT_SEARCH_DISTANCE_THRESHOLD
            ),
            sectors=sectors,
        )
        return [
            {
                "id": chunk.id,
                "content_preview": " ".join(chunk.content.split())[:CONTENT_PREVIEW_CHARS],
                "sector": chunk.sector,
                "budget_id": chunk.budget_id,
                "estimated_hours": chunk.estimated_hours,
                "distance": round(chunk.distance, 4),
            }
            for chunk in result.chunks
        ]

    return _backend


# The zero-arg default the loop uses when no per-run override is injected.
default_retrieval_backend: RetrievalBackend = make_retrieval_backend()
