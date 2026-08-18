"""Extract plain text from uploaded PDF / DOCX attachments.

Dispatch by extension. The caller is the session router, which holds the
``UploadFile`` objects from ``multipart/form-data``. Each extracted text is
truncated to ``max_chars`` to protect the prompt budget — real chunking and
retrieval enter in module 3.
"""

from __future__ import annotations

import io
from pathlib import PurePosixPath

import structlog

log = structlog.get_logger()


class AttachmentExtractionError(Exception):
    """Generic extraction failure (corrupt PDF, unreadable DOCX, …)."""

    def __init__(self, filename: str, message: str) -> None:
        super().__init__(message)
        self.filename = filename
        self.message = message


class UnsupportedAttachmentError(AttachmentExtractionError):
    """Raised when the file extension is not one we know how to handle."""


_SUPPORTED_EXTS = {".pdf", ".docx"}


def _extension(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


def _extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("pdf_page_extract_failed", error=str(exc)[:200])
            text = ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _extract_docx(content: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(content))
    paragraphs = [p.text for p in document.paragraphs if p.text and p.text.strip()]
    return "\n".join(paragraphs)


def extract_text(*, filename: str, content: bytes, max_chars: int) -> str:
    """Return extracted text for the attachment, truncated to ``max_chars``.

    Raises ``UnsupportedAttachmentError`` for unknown extensions and
    ``AttachmentExtractionError`` for parser failures.
    """
    ext = _extension(filename)
    if ext not in _SUPPORTED_EXTS:
        raise UnsupportedAttachmentError(
            filename,
            f"Unsupported attachment extension {ext!r}; supported: {sorted(_SUPPORTED_EXTS)}",
        )

    try:
        if ext == ".pdf":
            text = _extract_pdf(content)
        else:
            text = _extract_docx(content)
    except UnsupportedAttachmentError:
        raise
    except AttachmentExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AttachmentExtractionError(
            filename, f"Failed to extract text from {filename}: {exc}"
        ) from exc

    text = text.strip()
    if not text:
        log.info("attachment_extracted_empty", filename=filename)
        return ""

    if len(text) > max_chars:
        log.info(
            "attachment_truncated",
            filename=filename,
            original_chars=len(text),
            kept_chars=max_chars,
        )
        text = text[:max_chars]

    return text


def enrich_transcript(*, transcript: str, attachments: list[tuple[str, str]]) -> str:
    """Concatenate the original transcript with attachment sections.

    ``attachments`` is a list of ``(filename, extracted_text)`` tuples,
    already filtered for empty content. Each section is wrapped between a
    pair of explicit fences so the LLM sees the boundary and the source
    filename for each block.
    """
    if not attachments:
        return transcript

    parts = [transcript.strip()]
    for filename, text in attachments:
        if not text:
            continue
        parts.append(f"--- attachment: {filename} ---\n{text}\n--- end attachment ---")
    return "\n\n".join(parts)
