"""SQLAlchemy ORM models for the vector store (Session 8 + Session 10 multi-index).

``documents`` is one row per ingested source (a historical budget, a meeting
transcript, a technical doc) and owns provenance: where it came from, when, and
document-level metadata.

Chunk storage is **partitioned into one table per collection** (Session 10,
Article 5 "Opción B"): ``budget_chunks`` (the Session 8 ``chunks`` table,
renamed in migration 0004), ``transcript_chunks`` and ``technical_doc_chunks``.
The structural columns are identical across the three, so they share the
:class:`_ChunkColumns` declarative mixin; what diverges is the JSONB metadata
SCHEMA each collection carries (budgets: sector/year/budget_id; transcripts:
speakers/meeting_date; docs: version) — and "schemas that diverge → separate
tables" is exactly the rule the article establishes. Separate tables also mean a
separate vector index and lifecycle per collection, instead of one index where
the dominant type floods the top-k.

Design notes (defended in the README):

* ``metadata`` is a JSONB column on every table. Stable fields live in typed
  columns; whatever the chunker enriches goes to JSONB, queryable via the GIN
  index without a migration per new key.
* ``embedding`` is **nullable**: it allows inserting a chunk first and filling
  the vector later (async ingestion, future sessions).
* ``Vector(1536)`` is hardcoded to ``text-embedding-3-small``'s dimensionality;
  changing it means re-embedding the whole corpus, so it is not configuration.
* ``content_tsv`` is a STORED generated tsvector backing the lexical branch of
  hybrid search. The ``FTS_REGCONFIG`` config MUST match the corpus language and
  the ``plainto_tsquery`` call in the repository (see migration 0003's note: the
  shipped dataset is English, so the config is ``english``).

``metadata`` is a reserved attribute on SQLAlchemy declarative models, so the
Python attribute is ``metadata_`` mapped onto the ``"metadata"`` column.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Computed, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship
from sqlalchemy.types import DateTime

from app.foundation.persistence.models import Base

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small

# Postgres text-search configuration for the generated ``content_tsv`` column.
# It must match BOTH the corpus language AND the ``plainto_tsquery`` config in
# ``store/repository.py``; an index/query config mismatch silently bypasses the
# GIN index. The shipped corpus is English (see migration 0003 for the rationale).
FTS_REGCONFIG = "english"


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_source_path", "source_path"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Budget chunks keep the bidirectional relationship (Session 8/9 ORM-level
    # cascade). Transcript/doc chunks rely on the DB-level ON DELETE CASCADE only
    # — no ORM relationship is needed for them, which keeps ``documents`` from
    # having to disambiguate three back-references.
    chunks: Mapped[list[BudgetChunkRow]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class _ChunkColumns:
    """Columns shared by every chunk collection table (Session 10 multi-index).

    A declarative mixin: SQLAlchemy copies these ``mapped_column`` definitions
    onto each concrete table. The per-table indexes are generated from the
    concrete ``__tablename__`` so the three collections stay structurally
    identical without repeating the column list three times.
    """

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    # STORED generated tsvector — Postgres maintains it from ``content`` (no
    # trigger, no drift). Read-only at the ORM level.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{FTS_REGCONFIG}', content)", persisted=True),
        nullable=True,
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple:  # noqa: N805
        name = cls.__tablename__
        return (
            Index(f"ix_{name}_document_id", "document_id"),
            Index(f"ix_{name}_chunk_type", "chunk_type"),
            Index(f"ix_{name}_metadata_gin", "metadata", postgresql_using="gin"),
            Index(f"ix_{name}_content_tsv", "content_tsv", postgresql_using="gin"),
        )


class BudgetChunkRow(_ChunkColumns, Base):
    """Chunks of historical budgets (the Session 8 ``chunks`` table, renamed)."""

    __tablename__ = "budget_chunks"

    document: Mapped[DocumentRow] = relationship(back_populates="chunks")


class TranscriptChunkRow(_ChunkColumns, Base):
    """Chunks of client meeting transcripts (Session 10). Metadata carries
    ``speakers`` and ``meeting_date`` instead of a budget's sector/year."""

    __tablename__ = "transcript_chunks"


class TechnicalDocChunkRow(_ChunkColumns, Base):
    """Chunks of internal technical documentation (Session 10). Metadata carries
    a ``version`` instead of project/budget fields."""

    __tablename__ = "technical_doc_chunks"


# Backwards-compatibility alias: Session 8/9 code imports ``ChunkRow`` and means
# the budget collection. Keeping the alias avoids churning every existing import
# while the table is now named ``budget_chunks``.
ChunkRow = BudgetChunkRow
