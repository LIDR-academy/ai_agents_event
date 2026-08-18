"""Session 10 — full-text search column on chunks (lexical branch of hybrid search).

Revision ID: 0003_session10_fts
Revises: 0002_session8_pgvector
Create Date: 2026-06-19 00:00:00

Adds a STORED generated ``content_tsv`` column derived from ``content`` plus a
GIN index, so the hybrid retriever can run a keyword (BM25-style ``ts_rank_cd``)
branch alongside the existing vector branch and fuse both with RRF.

Text-search configuration note: the exercise statement says the corpus is in
Spanish, but the shipped dataset (``data/budgets_sample.json``,
``data/task_corpus.json``) is actually in ENGLISH ("Faceted search", "Order
lifecycle", …). We therefore use the ``english`` configuration: its stemming and
stop-word list are what actually lift lexical recall on this corpus. Swapping to
``spanish`` would only change the regconfig string in two places (here and the
``plainto_tsquery`` call in the repository) — both kept consistent on purpose.

The column is GENERATED ALWAYS … STORED: Postgres recomputes the tsvector on
every insert/update of ``content``, so there is no trigger to maintain and no way
for the lexical index to drift from the text it indexes.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003_session10_fts"
down_revision: Union[str, None] = "0002_session8_pgvector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated column: raw DDL is the clearest expression of a STORED generated
    # tsvector (Alembic/SQLAlchemy have no first-class helper for it).
    op.execute(
        "ALTER TABLE chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.create_index(
        "ix_chunks_content_tsv",
        "chunks",
        ["content_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_content_tsv", table_name="chunks")
    op.drop_column("chunks", "content_tsv")
