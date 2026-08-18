"""Session 6 initial schema — pseudonym_mappings + ingestion_jobs.

Revision ID: 0001_session6_initial
Revises:
Create Date: 2026-05-22 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001_session6_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pseudonym_mappings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("original_hash", sa.String(length=128), nullable=False),
        sa.Column("pseudonym", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "entity_type", "original_hash", name="uq_mappings_entity_hash"
        ),
    )
    op.create_index(
        "idx_mappings_lookup",
        "pseudonym_mappings",
        ["entity_type", "original_hash"],
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "documents_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_jobs_status", "ingestion_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("idx_jobs_status", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("idx_mappings_lookup", table_name="pseudonym_mappings")
    op.drop_table("pseudonym_mappings")
