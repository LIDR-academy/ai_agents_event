"""Tests for the budget JSON parser."""
from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.catalog.models import (
    CatalogDecision,
    CatalogSource,
    QualityScore,
    Sensitivity,
)
from app.ingestion.loaders.filesystem import LoadedBlob
from app.ingestion.parsers.budget_json import BudgetJsonParser
from app.ingestion.parsers.protocol import ParseContext


def _context(source_name: str = "presupuestos_json") -> ParseContext:
    return ParseContext(
        source=CatalogSource(
            name=source_name,
            location="budgets",
            format="json",
            quality=QualityScore(
                completeness=4, consistency=4, actuality=5, reliability=4
            ),
            sensitivity=Sensitivity(has_pii=True, pii_flags=["PERSON"]),
            decision=CatalogDecision.INCLUDE,
        ),
        source_version="1.0.0",
        ingested_at=datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc),
    )


def test_budget_parser_renders_structured_markdown():
    blob = LoadedBlob(
        relative_path="budgets/BUDGET-2024-0001.json",
        bytes_=(
            b'{"budget_id":"BUDGET-2024-0001","client_name":"Acme",'
            b'"client_code":"CLI-0042","currency":"EUR","total_amount":45000,'
            b'"signed_at":"2024-02-14",'
            b'"phases":[{"name":"Discovery","weeks":2,"amount":8000}],'
            b'"notes":"alpha"}'
        ),
    )
    docs = list(BudgetJsonParser().parse(blob, _context()))
    assert len(docs) == 1
    doc = docs[0]
    assert doc.id == "presupuestos_json:BUDGET-2024-0001:budgets/BUDGET-2024-0001.json"
    assert doc.text.startswith("# Presupuesto BUDGET-2024-0001")
    # Sections should be there as H2s
    for section in ("## Cliente", "## Total", "## Fases", "## Notas"):
        assert section in doc.text
    # And the numbers stay
    assert "45000 EUR" in doc.text


def test_budget_parser_propagates_metadata():
    blob = LoadedBlob(
        relative_path="budgets/x.json",
        bytes_=(
            b'{"budget_id":"BUDGET-2024-0099","currency":"EUR","total_amount":1,'
            b'"signed_at":"2024-01-01","phases":[]}'
        ),
    )
    docs = list(BudgetJsonParser().parse(blob, _context()))
    meta = docs[0].metadata
    assert meta.source_name == "presupuestos_json"
    assert meta.source_version == "1.0.0"
    assert meta.location == "budgets/x.json"
    assert meta.extra["budget_id"] == "BUDGET-2024-0099"
    assert meta.extra["currency"] == "EUR"
