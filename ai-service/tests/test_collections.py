"""Unit tests for the collection registry (multi-index metadata layer).

Pure introspection — no DB. Pins the per-collection knowledge the rest of the
pipeline relies on: rule routing, date extraction for decay, the uniform chunk
mapping, and the hard-filter clauses each collection understands.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.generation.rag.retrieval.collections import (
    ALL_COLLECTIONS,
    Collection,
    HardFilters,
    match_rules,
    spec_for,
)
from app.generation.rag.store.models import (
    BudgetChunkRow,
    TechnicalDocChunkRow,
    TranscriptChunkRow,
)


def test_registry_maps_each_collection_to_its_table():
    assert spec_for(Collection.BUDGET).model is BudgetChunkRow
    assert spec_for(Collection.TRANSCRIPT).model is TranscriptChunkRow
    assert spec_for(Collection.TECHNICAL_DOC).model is TechnicalDocChunkRow
    assert set(ALL_COLLECTIONS) == set(Collection)


def test_rules_route_by_vocabulary():
    assert match_rules("how much did the budget cost in hours") == [Collection.BUDGET]
    assert match_rules("what did the client say in the kickoff meeting") == [Collection.TRANSCRIPT]
    assert match_rules("API reference for the architecture spec") == [Collection.TECHNICAL_DOC]
    assert match_rules("the weather today") == []  # no signal → escalate


def test_date_extraction_per_collection():
    assert spec_for(Collection.BUDGET).date_of({"year": 2021}) == date(2021, 1, 1)
    assert spec_for(Collection.TRANSCRIPT).date_of({"meeting_date": "2023-09-10"}) == date(
        2023, 9, 10
    )
    # Technical docs carry no usable date; bad/missing values degrade to None.
    assert spec_for(Collection.TECHNICAL_DOC).date_of({"version": "v1.4"}) is None
    assert spec_for(Collection.BUDGET).date_of({}) is None
    assert spec_for(Collection.TRANSCRIPT).date_of({"meeting_date": "not-a-date"}) is None


def test_to_chunk_carries_provenance_and_source_id():
    row = SimpleNamespace(
        id=7,
        content="meeting about Stripe",
        chunk_type="meeting_segment",
        metadata_={"transcript_id": "TR-2024-002", "meeting_date": "2024-03-20"},
    )
    chunk = spec_for(Collection.TRANSCRIPT).to_chunk(row, distance=0.3)
    assert chunk.collection == "transcript"
    assert chunk.source_id == "TR-2024-002"
    assert chunk.document_date == date(2024, 3, 20)


def test_hard_filters_emit_only_axes_the_collection_understands():
    filters = HardFilters(
        technologies=("node",), sectors=("ecommerce",), min_date=date(2023, 1, 1), version="v3.2"
    )
    # Budgets understand sector + technology + min_date (→ 3 clauses); not version.
    assert len(spec_for(Collection.BUDGET).hard_filter_clauses(filters)) == 3
    # Transcripts understand min_date only.
    assert len(spec_for(Collection.TRANSCRIPT).hard_filter_clauses(filters)) == 1
    # Technical docs understand version only.
    assert len(spec_for(Collection.TECHNICAL_DOC).hard_filter_clauses(filters)) == 1
    # No filters → no clauses (search behaves unconstrained).
    assert spec_for(Collection.BUDGET).hard_filter_clauses(HardFilters()) == []
