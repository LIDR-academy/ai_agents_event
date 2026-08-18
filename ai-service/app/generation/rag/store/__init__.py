"""pgvector persistence for the RAG layer (Session 8).

``models`` defines the ``documents``/``chunks`` tables; ``repository`` is the
async data-access layer. Sessions are owned by the callers (ingest service /
retriever) so a whole ingest fits in one transaction. No vector index yet —
the live session adds HNSW on top of this baseline.
"""

from app.generation.rag.store.models import ChunkRow, DocumentRow
from app.generation.rag.store.repository import ChunkStore

__all__ = ["ChunkRow", "ChunkStore", "DocumentRow"]
