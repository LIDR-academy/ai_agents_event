"""Ingest orchestration: chunk → embed → persist, in ONE transaction.

Composes the structural chunker, the OpenAI embedder and the pgvector store —
all of them RAG-internal collaborators, so this composition lives inside the
``generation/rag`` sibling (the conductor rule only constrains *cross*-sibling
composition).

The single transaction is the point: the document row is inserted first (so
chunks can FK it via ``flush``), but if the embeddings API fails afterwards the
whole transaction rolls back and no orphan ``documents`` row survives.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.generation.rag.chunking.structural import JSONStructuralChunker
from app.generation.rag.embedding.embedder import OpenAIEmbedder
from app.generation.rag.schemas import Budget, IngestResponse
from app.generation.rag.store.repository import ChunkStore

log = structlog.get_logger()


class DuplicateDocumentError(Exception):
    """A document with the same ``source_path`` is already ingested."""

    def __init__(self, document_id: int) -> None:
        super().__init__(f"Document already ingested (id={document_id})")
        self.document_id = document_id


class RagIngestService:
    """Persists one budget as a document + its embedded chunks."""

    def __init__(
        self,
        chunker: JSONStructuralChunker,
        embedder: OpenAIEmbedder,
        session_factory: async_sessionmaker,
        store: ChunkStore,
    ) -> None:
        self._chunker = chunker
        self._embedder = embedder
        self._session_factory = session_factory
        self._store = store

    async def ingest(
        self,
        *,
        source_path: str,
        document_type: str,
        budget: Budget,
        chunk_type: str = "budget_component",
    ) -> IngestResponse:
        started = time.perf_counter()

        async with self._session_factory() as session, session.begin():
            # 1. Duplicate guard. App-level check-then-insert: not race-proof
            #    under concurrent identical ingests, fine at teaching scale.
            existing_id = await self._store.find_document_id(session, source_path)
            if existing_id is not None:
                raise DuplicateDocumentError(existing_id)

            # 2. Structural chunking (one chunk per budget component).
            chunks = self._chunker.chunk([budget])

            # 3. Batch embedding — one embeddings.create per ≤100-chunk batch.
            #    The OpenAI client is sync; run it in a thread so the event
            #    loop keeps serving requests. If this raises, the transaction
            #    rolls back: no orphan document.
            embedded = await asyncio.to_thread(self._embedder.embed_many, chunks)

            # 4. Document row + all chunk rows (add_all), still uncommitted.
            document_id = await self._store.persist_document_with_chunks(
                session,
                source_path=source_path,
                document_type=document_type,
                doc_metadata={
                    "budget_id": budget.budget_id,
                    "client_sector": budget.client_metadata.sector,
                    "year": budget.year,
                },
                embedded_chunks=embedded,
                chunk_type=chunk_type,
            )
            # 5. Commit on scope exit (session.begin()).

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response = IngestResponse(
            document_id=document_id,
            chunks_created=len(embedded),
            embedding_dimension=len(embedded[0].embedding) if embedded else 0,
            ingestion_time_ms=elapsed_ms,
        )
        log.info("rag_ingest_persisted", source_path=source_path, **response.model_dump())
        return response
