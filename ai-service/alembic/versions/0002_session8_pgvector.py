"""Session 8 — pgvector extension + documents/chunks tables.

Revision ID: 0002_session8_pgvector
Revises: 0001_session6_initial
Create Date: 2026-06-04 00:00:00

Deliberately NO vector index (HNSW / IVFFlat): the sequential scan is the
baseline the live session measures the index impact against. The non-vector
indexes (FK, chunk_type, GIN on metadata) ARE created here because they are
plain relational hygiene, not the subject of the live demo.

``ix_documents_source_path`` is a plain (non-unique) index, literal to the
exercise statement; uniqueness of ``source_path`` is enforced by the
application-level duplicate check that returns 409. That check-then-insert is
not race-proof under concurrent identical ingests — acceptable at teaching
scale, and a nice discussion hook for the live session.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002_session8_pgvector"
down_revision: Union[str, None] = "0001_session6_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Must run before any Vector column exists. The pgvector/pgvector:pg16
    # image ships the extension compiled; this single statement activates it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_documents_source_path", "documents", ["source_path"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.BigInteger,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_type", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        # Nullable: leaves the door open to insert-now-embed-later ingestion
        # (future sessions). Session 8 always writes chunk+vector atomically.
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
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_chunk_type", "chunks", ["chunk_type"])
    # GIN over the whole JSONB: query arbitrary metadata keys without a
    # schema migration per new key.
    op.create_index("ix_chunks_metadata_gin", "chunks", ["metadata"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_metadata_gin", table_name="chunks")
    op.drop_index("ix_chunks_chunk_type", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_documents_source_path", table_name="documents")
    op.drop_table("documents")
    # The vector extension is left installed: dropping an extension is a
    # cluster-level operation that could break other objects, and re-creating
    # it is free (IF NOT EXISTS) on the next upgrade.
