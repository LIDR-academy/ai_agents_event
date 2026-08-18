"""Tests for the transcript TXT parser (tagged + legacy modes)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.catalog.models import (
    CatalogDecision,
    CatalogSource,
    QualityScore,
    Sensitivity,
)
from app.ingestion.loaders.filesystem import LoadedBlob
from app.ingestion.parsers.protocol import ParseContext
from app.ingestion.parsers.transcript_txt import TranscriptTxtParser


def _context() -> ParseContext:
    return ParseContext(
        source=CatalogSource(
            name="transcripciones_txt",
            location="transcripts",
            format="txt",
            quality=QualityScore(
                completeness=3, consistency=2, actuality=4, reliability=3
            ),
            sensitivity=Sensitivity(has_pii=True, pii_flags=["PERSON"]),
            decision=CatalogDecision.INCLUDE,
        ),
        source_version="1.0.0",
        ingested_at=datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc),
    )


def _blob(text: str, relpath: str = "transcripts/x.txt") -> LoadedBlob:
    return LoadedBlob(relative_path=relpath, bytes_=text.encode("utf-8"))


TAGGED = """\
[10:00:12] Laura: hola, buenos días
[10:00:48] Diego: hola Laura, te escucho
[10:01:30] Laura: hablemos del presupuesto BUDGET-2024-0001
[10:02:10] Diego: anotado
"""

LEGACY = """\
Reunión de seguimiento sin marcadores horarios.

Carmen abre la sesión recordando que el cliente CLI-0288 sigue activo.

Cierre tras consenso del importe revisado.
"""


def test_tagged_mode_yields_one_doc_per_turn():
    docs = list(TranscriptTxtParser().parse(_blob(TAGGED), _context()))
    assert len(docs) == 4
    assert docs[0].text == "hola, buenos días"
    assert docs[0].metadata.extra["speaker"] == "Laura"
    assert docs[0].metadata.extra["timestamp"] == "10:00:12"
    assert docs[0].metadata.extra["format_mode"] == "tagged"


def test_legacy_mode_yields_one_doc_per_block():
    docs = list(TranscriptTxtParser().parse(_blob(LEGACY), _context()))
    # Three non-empty blocks separated by blank lines.
    assert len(docs) == 3
    assert all(doc.metadata.extra["format_mode"] == "legacy" for doc in docs)
    assert all("speaker" not in doc.metadata.extra for doc in docs)


def test_legacy_mode_detected_below_three_matches():
    """A single tagged-looking line is not enough — it stays in legacy mode."""
    almost = "Texto plano sin tags.\n\n[10:00:00] Speaker: only one tagged line\n"
    docs = list(TranscriptTxtParser().parse(_blob(almost), _context()))
    assert all(doc.metadata.extra["format_mode"] == "legacy" for doc in docs)
