"""Async data-access layer for the vector store.

The store never opens or commits sessions: the caller (ingest service,
retriever) owns the ``AsyncSession`` so a whole ingest — duplicate check,
document row, chunk rows — fits in ONE transaction. A failure anywhere rolls
everything back and leaves no orphan ``documents`` row.

Session 10 makes every search/persist method **collection-aware**: a ``model``
argument selects which chunk table (``budget_chunks`` / ``transcript_chunks`` /
``technical_doc_chunks``) the query runs against. It defaults to the budgets
table so all Session 8/9 callers are unaffected.
"""

from __future__ import annotations

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Integer, Row, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.generation.rag.schemas import EmbeddedChunk
from app.generation.rag.store.models import (
    BudgetChunkRow,
    DocumentRow,
    EMBEDDING_DIMENSIONS,
    TechnicalDocChunkRow,
    TranscriptChunkRow,
)

# The structural chunker emits one chunk per budget component; the vocabulary
# is queryable thanks to the index on ``chunk_type`` (live-session filters).
BUDGET_COMPONENT = "budget_component"


class ChunkStore:
    """CRUD + similarity search over ``documents`` and the chunk tables."""

    async def find_document_id(self, session: AsyncSession, source_path: str) -> int | None:
        """Return the id of the document already ingested from ``source_path``,
        or ``None``. Backs the application-level 409 duplicate guard."""
        stmt = select(DocumentRow.id).where(DocumentRow.source_path == source_path)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def corpus_stats(self, session: AsyncSession) -> list[tuple[str, int, int, bool]]:
        """Per-collection ``(name, documents, chunks, hnsw_indexed)`` snapshot.

        Backs the Session 11 corpus-expansion UI: it shows the corpus growing
        and whether each collection carries a vector index. Documents are counted
        as the distinct parent documents that own chunks in the collection."""
        collections = (
            ("budget", BudgetChunkRow),
            ("transcript", TranscriptChunkRow),
            ("technical_doc", TechnicalDocChunkRow),
        )
        index_exists_stmt = text(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = :name)"
        )
        out: list[tuple[str, int, int, bool]] = []
        for name, model in collections:
            chunks = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            documents = (
                await session.execute(select(func.count(func.distinct(model.document_id))))
            ).scalar_one()
            indexed = (
                await session.execute(
                    index_exists_stmt, {"name": f"ix_{model.__tablename__}_embedding_hnsw"}
                )
            ).scalar_one()
            out.append((name, int(documents), int(chunks), bool(indexed)))
        return out

    async def persist_document_with_chunks(
        self,
        session: AsyncSession,
        *,
        source_path: str,
        document_type: str,
        doc_metadata: dict,
        embedded_chunks: list[EmbeddedChunk],
        chunk_type: str = BUDGET_COMPONENT,
        model: type = BudgetChunkRow,
    ) -> int:
        """Insert the document row plus all its chunk rows. No commit here —
        the caller's transaction decides when (and whether) anything lands.

        ``chunk_type`` is stamped on every chunk (filterable column); it
        defaults to ``budget_component`` so existing callers are unaffected.
        ``model`` selects the target chunk table (Session 10 multi-index)."""
        document = DocumentRow(
            source_path=source_path,
            document_type=document_type,
            metadata_=doc_metadata,
        )
        session.add(document)
        await session.flush()  # assigns document.id without committing

        session.add_all(
            model(
                document_id=document.id,
                chunk_type=chunk_type,
                content=chunk.text,
                embedding=chunk.embedding,
                metadata_=chunk.metadata,
            )
            for chunk in embedded_chunks
        )
        return document.id

    async def search(
        self,
        session: AsyncSession,
        *,
        query_vector: list[float],
        k: int,
        model: type = BudgetChunkRow,
    ) -> list[Row]:
        """k nearest chunks by cosine distance (``<=>``), sequential scan.

        Cosine over L2/inner product: OpenAI embeddings are normalized so the
        ranking would be equivalent, but cosine keeps us aligned with the RAG
        literature AND with the ``vector_cosine_ops`` operator class of the
        HNSW index the live session adds — operator/index mismatch makes
        Postgres silently ignore the index.
        """
        distance = cast(model.embedding, HALFVEC(EMBEDDING_DIMENSIONS)).cosine_distance(
            query_vector
        )
        stmt = (
            select(
                model.id,
                model.document_id,
                model.chunk_type,
                model.content,
                model.metadata_,
                distance.label("distance"),
            )
            .order_by(distance)
            .limit(k)
        )
        return list((await session.execute(stmt)).all())

    async def search_filtered(
        self,
        session: AsyncSession,
        *,
        query_vector: list[float],
        top_k: int = 10,
        distance_threshold: float = 0.6,
        sectors: list[str] | None = None,
        project_year_min: int | None = None,
        project_year_max: int | None = None,
        chunk_types: list[str] | None = None,
        model: type = BudgetChunkRow,
        extra_filters: list | None = None,
    ) -> tuple[list[Row], int]:
        """k-NN search with structural pre-filtering and a relevance threshold.

        Structural filters (sector / project year / chunk type) narrow the
        candidate space BEFORE the vector ranking — the metadata is persisted in
        JSONB (``client_sector``, ``year``) and the ``chunk_type`` column. Each
        filter follows the ``(:filter IS NULL OR …)`` pattern: a ``None`` filter
        simply does not apply. The distance threshold then drops chunks that are
        not actually close (no "confidently retrieving garbage").

        ``model`` selects the collection table; ``extra_filters`` are
        pre-built SQLAlchemy predicates (the Session 10 hard filters embedded by
        the collection registry) that are ANDed with the structural ones.

        Returns
        -------
        tuple[list[Row], int]
            ``(rows, candidates_evaluated)`` where ``rows`` are the top-k chunks
            under the threshold (ascending distance) and ``candidates_evaluated``
            is how many chunks matched the filters before the threshold/limit
            were applied — the cardinality to watch for a silent empty result.
        """
        structural_filters = self._structural_filters(
            model,
            sectors=sectors,
            project_year_min=project_year_min,
            project_year_max=project_year_max,
            chunk_types=chunk_types,
        )
        structural_filters.extend(extra_filters or [])

        distance = cast(model.embedding, HALFVEC(EMBEDDING_DIMENSIONS)).cosine_distance(
            query_vector
        )

        count_stmt = select(func.count()).select_from(model).where(*structural_filters)
        candidates_evaluated = int((await session.execute(count_stmt)).scalar_one())

        stmt = (
            select(
                model.id,
                model.document_id,
                model.chunk_type,
                model.content,
                model.metadata_,
                distance.label("distance"),
            )
            .where(*structural_filters)
            .where(distance <= distance_threshold)
            .order_by(distance)
            .limit(top_k)
        )
        rows = list((await session.execute(stmt)).all())
        return rows, candidates_evaluated

    async def search_lexical(
        self,
        session: AsyncSession,
        *,
        query_text: str,
        top_k: int = 50,
        sectors: list[str] | None = None,
        project_year_min: int | None = None,
        project_year_max: int | None = None,
        chunk_types: list[str] | None = None,
        model: type = BudgetChunkRow,
        extra_filters: list | None = None,
    ) -> list[Row]:
        """Keyword (full-text) ranking over the ``content_tsv`` column (Session 10).

        The lexical branch of hybrid search: ``plainto_tsquery`` turns the query
        into a tsquery (AND of its lexemes, stop-words dropped), ``@@`` keeps only
        chunks that match, and ``ts_rank_cd`` (cover-density) ranks them — higher
        is better, opposite to vector distance. The ``english`` config MUST match
        the generated column's config (``models.FTS_REGCONFIG``) or the GIN index
        is bypassed and matching silently changes. Structural/hard filters mirror
        ``search_filtered`` so the two branches see the same candidate space.

        Returns rows ordered by rank DESC (most relevant first), capped at
        ``top_k``. ``rank`` rides along for debugging; fusion only uses ordering.
        """
        tsquery = func.plainto_tsquery("english", query_text)
        rank = func.ts_rank_cd(model.content_tsv, tsquery)

        structural_filters = self._structural_filters(
            model,
            sectors=sectors,
            project_year_min=project_year_min,
            project_year_max=project_year_max,
            chunk_types=chunk_types,
        )
        structural_filters.extend(extra_filters or [])

        stmt = (
            select(
                model.id,
                model.document_id,
                model.chunk_type,
                model.content,
                model.metadata_,
                rank.label("rank"),
            )
            .where(model.content_tsv.op("@@")(tsquery))
            .where(*structural_filters)
            .order_by(rank.desc())
            .limit(top_k)
        )
        return list((await session.execute(stmt)).all())

    @staticmethod
    def _structural_filters(
        model: type,
        *,
        sectors: list[str] | None,
        project_year_min: int | None,
        project_year_max: int | None,
        chunk_types: list[str] | None,
    ) -> list:
        """Shared ``(metadata/chunk_type)`` predicates for filtered + lexical search.

        Each filter follows the ``None`` means "do not filter on this axis"
        convention; sector/year read from JSONB, chunk_type from its own column.
        """
        sector_col = model.metadata_["client_sector"].astext
        year_col = cast(model.metadata_["year"].astext, Integer)

        filters = []
        if sectors:
            filters.append(sector_col.in_(sectors))
        if project_year_min is not None:
            filters.append(year_col >= project_year_min)
        if project_year_max is not None:
            filters.append(year_col <= project_year_max)
        if chunk_types:
            filters.append(model.chunk_type.in_(chunk_types))
        return filters
