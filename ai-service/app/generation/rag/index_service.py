"""Session 11 — incremental corpus expansion (add NEW documents + index them).

Grows the vector DB with new information, one budget document at a time, reusing
:class:`RagIngestService` (chunk → embed → persist, with the duplicate guard) so
no ingest logic is re-implemented. This is a thin batch loop on top of it that:

* skips documents already present (same ``source_path``) instead of failing the
  whole batch,
* reports per-document progress through an ``on_progress`` callback so the async
  job the router runs can surface a live counter to the UI.

Not re-embedding the existing corpus (that would be a model change / blue-green
migration, out of scope): the new documents are embedded with the SAME model and
land in ``budget_chunks``, where the Session 11 HNSW index picks them up
automatically (HNSW is maintained incrementally on insert).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import structlog

from app.generation.rag.ingest_service import DuplicateDocumentError, RagIngestService
from app.generation.rag.schemas import Budget

log = structlog.get_logger()


@dataclass(frozen=True)
class CorpusExpansionResult:
    """Outcome of a batch corpus expansion."""

    documents_indexed: int
    documents_skipped: int
    chunks_created: int


class CorpusIndexService:
    """Batch-adds new documents to the corpus, reusing :class:`RagIngestService`."""

    def __init__(self, ingest: RagIngestService) -> None:
        self._ingest = ingest

    async def expand(
        self,
        documents: list[Budget],
        *,
        document_type: str = "historical_budget",
        chunk_type: str = "budget_component",
        source_prefix: str = "corpus-expansion",
        on_progress: Callable[[int], None] | None = None,
    ) -> CorpusExpansionResult:
        """Add every document in ``documents`` to the corpus.

        A document already present (same ``source_path``) is skipped, not an
        error. ``on_progress`` is called with the running processed count
        (indexed + skipped) after each document, so a job can report progress.
        """
        indexed = skipped = chunks = 0
        for document in documents:
            source_path = f"{source_prefix}::{document.budget_id}"
            try:
                response = await self._ingest.ingest(
                    source_path=source_path,
                    document_type=document_type,
                    budget=document,
                    chunk_type=chunk_type,
                )
                indexed += 1
                chunks += response.chunks_created
            except DuplicateDocumentError:
                skipped += 1
                log.info("corpus_expansion_skip_duplicate", source_path=source_path)
            if on_progress is not None:
                on_progress(indexed + skipped)

        log.info(
            "corpus_expansion_done",
            documents_indexed=indexed,
            documents_skipped=skipped,
            chunks_created=chunks,
        )
        return CorpusExpansionResult(
            documents_indexed=indexed,
            documents_skipped=skipped,
            chunks_created=chunks,
        )
