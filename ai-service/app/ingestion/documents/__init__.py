"""The canonical ``Document`` contract.

Every parser produces ``Document`` instances. Every downstream consumer
(cleaning, validation, PII, future RAG) accepts ``Document`` instances. Keeping
this model deliberately *flat* (no nested business types) makes it trivial to
fake in tests and homogenous across formats.
"""

from app.ingestion.documents.models import Document, DocumentMetadata

__all__ = ["Document", "DocumentMetadata"]
