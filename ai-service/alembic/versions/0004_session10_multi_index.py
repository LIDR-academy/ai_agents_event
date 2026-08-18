"""Session 10 — multi-index: rename chunks→budget_chunks, add transcript/doc tables.

Revision ID: 0004_session10_multi_index
Revises: 0003_session10_fts
Create Date: 2026-06-23 00:00:00

Article 5 ("Opción B"): partition the heterogeneous corpus into one table per
collection so each has its own metadata schema, vector index and lifecycle,
instead of a single ``chunks`` table where the dominant document type floods the
top-k. This migration:

1. Renames the Session 8 ``chunks`` table (and its indexes) to ``budget_chunks``.
   The STORED generated ``content_tsv`` column added in 0003 travels with the
   rename — no need to recreate it.
2. Creates ``transcript_chunks`` and ``technical_doc_chunks`` with the SAME
   structural columns (they share the ``_ChunkColumns`` ORM mixin) but their own
   JSONB metadata schema, GIN indexes and ``content_tsv`` lexical column.

Text-search configuration stays ``english`` everywhere (corpus language; see the
note in 0003). No vector (HNSW) index is created here on purpose: vector indexes
are the subject of the Session 8 live demo and live in a manual SQL script, not
in Alembic — the new tables run on a sequential scan at teaching scale, exactly
like the budgets table did before its live-session index.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0004_session10_multi_index"
down_revision: Union[str, None] = "0003_session10_fts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FTS_REGCONFIG = "english"
NEW_COLLECTIONS = ("transcript_chunks", "technical_doc_chunks")

# Index renames for the chunks→budget_chunks table (old, new).
_BUDGET_INDEX_RENAMES = (
    ("ix_chunks_document_id", "ix_budget_chunks_document_id"),
    ("ix_chunks_chunk_type", "ix_budget_chunks_chunk_type"),
    ("ix_chunks_metadata_gin", "ix_budget_chunks_metadata_gin"),
    ("ix_chunks_content_tsv", "ix_budget_chunks_content_tsv"),
)


def _create_chunk_table(table: str) -> None:
    """Create one collection table identical in shape to ``budget_chunks``."""
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.BigInteger,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_type", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # STORED generated tsvector (raw DDL — no first-class Alembic helper for it).
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN content_tsv tsvector "
        f"GENERATED ALWAYS AS (to_tsvector('{FTS_REGCONFIG}', content)) STORED"
    )
    op.create_index(f"ix_{table}_document_id", table, ["document_id"])
    op.create_index(f"ix_{table}_chunk_type", table, ["chunk_type"])
    op.create_index(f"ix_{table}_metadata_gin", table, ["metadata"], postgresql_using="gin")
    op.create_index(f"ix_{table}_content_tsv", table, ["content_tsv"], postgresql_using="gin")


def _drop_chunk_table(table: str) -> None:
    op.drop_index(f"ix_{table}_content_tsv", table_name=table)
    op.drop_index(f"ix_{table}_metadata_gin", table_name=table)
    op.drop_index(f"ix_{table}_chunk_type", table_name=table)
    op.drop_index(f"ix_{table}_document_id", table_name=table)
    op.drop_table(table)


def upgrade() -> None:
    # 1. Rename the budgets table + its indexes to the collection name.
    op.rename_table("chunks", "budget_chunks")
    for old, new in _BUDGET_INDEX_RENAMES:
        op.execute(f"ALTER INDEX {old} RENAME TO {new}")

    # 2. Create the two new collections.
    for table in NEW_COLLECTIONS:
        _create_chunk_table(table)


def downgrade() -> None:
    for table in NEW_COLLECTIONS:
        _drop_chunk_table(table)

    for old, new in _BUDGET_INDEX_RENAMES:
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    op.rename_table("budget_chunks", "chunks")
