"""Local attachment text extraction (Camino B).

We extract text from PDF and DOCX uploads inside the estimator and splice
it into the prompt as additional context. The alternative (Camino A) is to
hand the binary to a multimodal provider via its Files API; we deliberately
chose Camino B because it keeps the LLM wrapper provider-agnostic (text in,
text out) and prepares the ground for chunking/RAG in module 3.
"""

from app.foundation.attachments.extractor import (
    AttachmentExtractionError,
    UnsupportedAttachmentError,
    extract_text,
)

__all__ = [
    "AttachmentExtractionError",
    "UnsupportedAttachmentError",
    "extract_text",
]
