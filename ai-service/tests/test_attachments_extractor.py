"""Unit tests for the local attachment text extractor (Camino B)."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest

from app.foundation.attachments.extractor import (
    AttachmentExtractionError,
    UnsupportedAttachmentError,
    enrich_transcript,
    extract_text,
)


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for line in paragraphs:
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_docx_extraction_returns_joined_paragraphs() -> None:
    content = _docx_bytes(["Project Nimbus", "React + Postgres", "Phase 1: discovery"])
    out = extract_text(filename="spec.docx", content=content, max_chars=10_000)
    assert "Project Nimbus" in out
    assert "React + Postgres" in out
    assert "Phase 1: discovery" in out


def test_docx_extraction_truncates_at_max_chars() -> None:
    content = _docx_bytes(["a" * 5_000, "b" * 5_000])
    out = extract_text(filename="long.docx", content=content, max_chars=1_000)
    assert len(out) == 1_000


def test_unsupported_extension_raises() -> None:
    with pytest.raises(UnsupportedAttachmentError):
        extract_text(filename="picture.png", content=b"", max_chars=100)


def test_pdf_extraction_calls_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """We mock pypdf to keep the test independent of real PDF binaries.
    The integration tests exercise the real path with a fixture upload."""
    fake_pages = [
        SimpleNamespace(extract_text=lambda: "Page one text"),
        SimpleNamespace(extract_text=lambda: "Page two text"),
    ]
    fake_reader = SimpleNamespace(pages=fake_pages)

    def fake_pdf_reader(stream: Any) -> SimpleNamespace:  # noqa: ARG001
        return fake_reader

    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", fake_pdf_reader)

    out = extract_text(filename="proposal.pdf", content=b"%PDF-fake", max_chars=10_000)
    assert "Page one text" in out
    assert "Page two text" in out


def test_pdf_extraction_swallows_per_page_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_text() -> str:
        raise RuntimeError("corrupt page")

    fake_pages = [
        SimpleNamespace(extract_text=fail_text),
        SimpleNamespace(extract_text=lambda: "Recoverable page"),
    ]
    fake_reader = SimpleNamespace(pages=fake_pages)

    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", lambda _stream: fake_reader)

    out = extract_text(filename="mixed.pdf", content=b"%PDF-fake", max_chars=10_000)
    assert "Recoverable page" in out


def test_pdf_extraction_wraps_global_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_stream: Any) -> SimpleNamespace:
        raise ValueError("not a pdf")

    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", boom)

    with pytest.raises(AttachmentExtractionError):
        extract_text(filename="bad.pdf", content=b"xxxx", max_chars=100)


def test_enrich_transcript_wraps_attachments_with_fences() -> None:
    out = enrich_transcript(
        transcript="Project request: build a CRM.",
        attachments=[
            ("spec.pdf", "Functional spec body."),
            ("notes.docx", "Internal notes."),
        ],
    )
    assert "Project request" in out
    assert "--- attachment: spec.pdf ---" in out
    assert "Functional spec body." in out
    assert "--- attachment: notes.docx ---" in out
    assert "--- end attachment ---" in out


def test_enrich_transcript_returns_original_when_no_attachments() -> None:
    out = enrich_transcript(transcript="hello", attachments=[])
    assert out == "hello"


def test_enrich_transcript_skips_empty_attachments() -> None:
    out = enrich_transcript(
        transcript="x",
        attachments=[("empty.pdf", ""), ("real.docx", "content")],
    )
    assert "empty.pdf" not in out
    assert "real.docx" in out
