"""Session 11 — HNSW vector indexes on the three chunk collections.

Migration 0004 deliberately left ``budget_chunks`` / ``transcript_chunks`` /
``technical_doc_chunks`` on a sequential scan. As the corpus GROWS (Session 11
adds incremental corpus expansion), that scan gets linearly slower, so this
migration provisions the vector index — and, crucially, does it so the index is
actually USED.

The retriever searches by casting the stored ``vector(1536)`` to ``halfvec`` and
using cosine distance (see ``store/repository.py`` — ``cast(embedding,
HALFVEC(1536)).cosine_distance(...)``). An HNSW index is only consulted when its
operator class matches the query operator: a ``vector_cosine_ops`` index (the
Session 8 script) is **silently ignored** by a halfvec search. So the index here
is built on the SAME ``embedding::halfvec(1536)`` expression with
``halfvec_cosine_ops`` — half the index size AND a match for the query path.

Once the index exists, HNSW is maintained INCREMENTALLY: every chunk inserted by
the corpus-expansion pipeline is indexed automatically, no rebuild needed.

``m=16`` / ``ef_construction=128`` mirror the Session 8 live-session choices.
``IF NOT EXISTS`` keeps it idempotent across the branch-switch reruns the repo is
prone to.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0005_session11_hnsw_multi_index"
down_revision: Union[str, None] = "0004_session10_multi_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHUNK_TABLES = ("budget_chunks", "transcript_chunks", "technical_doc_chunks")
EMBEDDING_DIMENSIONS = 1536
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 128


def _index_name(table: str) -> str:
    return f"ix_{table}_embedding_hnsw"


def upgrade() -> None:
    for table in CHUNK_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {_index_name(table)} "
            f"ON {table} "
            f"USING hnsw ((embedding::halfvec({EMBEDDING_DIMENSIONS})) halfvec_cosine_ops) "
            f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})"
        )


def downgrade() -> None:
    for table in CHUNK_TABLES:
        op.execute(f"DROP INDEX IF EXISTS {_index_name(table)}")
