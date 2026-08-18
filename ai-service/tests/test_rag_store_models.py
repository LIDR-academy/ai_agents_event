"""Schema-level tests for the Session 8 ORM models.

Pure metadata introspection — no engine, no database. These pin the design
decisions the exercise asks students to defend: the reserved-name mapping
(``metadata_`` → column ``"metadata"``), the 1536-dim nullable vector, the
CASCADE FK and the deliberate absence of a vector index.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

from app.generation.rag.store.models import ChunkRow, DocumentRow


def test_metadata_attribute_maps_to_metadata_column():
    assert DocumentRow.metadata_.expression.name == "metadata"
    assert ChunkRow.metadata_.expression.name == "metadata"


def test_embedding_is_a_nullable_1536_dim_vector():
    embedding = ChunkRow.__table__.c.embedding
    assert isinstance(embedding.type, Vector)
    assert embedding.type.dim == 1536
    # Nullable on purpose: leaves the door open to insert-now-embed-later.
    assert embedding.nullable is True


def test_chunks_fk_cascades_on_document_delete():
    fk = next(iter(ChunkRow.__table__.c.document_id.foreign_keys))
    assert fk.column.table.name == "documents"
    assert fk.ondelete == "CASCADE"


def test_no_vector_index_yet():
    # The live session adds HNSW; the previo ships sequential scan only.
    index_columns = {col.name for index in ChunkRow.__table__.indexes for col in index.columns}
    assert "embedding" not in index_columns


def test_content_tsv_is_a_generated_fulltext_column():
    # Session 10: STORED generated tsvector backing the lexical search branch.
    content_tsv = ChunkRow.__table__.c.content_tsv
    assert isinstance(content_tsv.type, TSVECTOR)
    assert content_tsv.computed is not None  # GENERATED ALWAYS AS ...
    assert content_tsv.computed.persisted is True  # ... STORED


def test_relational_indexes_present():
    # Session 10 renamed chunks → budget_chunks; index names follow the table.
    index_names = {index.name for index in ChunkRow.__table__.indexes}
    assert index_names == {
        "ix_budget_chunks_document_id",
        "ix_budget_chunks_chunk_type",
        "ix_budget_chunks_metadata_gin",
        "ix_budget_chunks_content_tsv",  # GIN index for full-text search
    }
    assert {index.name for index in DocumentRow.__table__.indexes} == {"ix_documents_source_path"}
