"""Collection registry for the multi-index retriever (Session 10, Article 5).

The corpus is heterogeneous — historical budgets, meeting transcripts, internal
technical docs — and lives in three separate tables (see ``store/models.py``).
This module is the single place that knows, per collection:

* which ORM model backs it,
* how to read a date from its (divergent) metadata schema for temporal decay,
* how to map one of its DB rows onto the uniform :class:`RetrievedChunk`
  contract (so every downstream stage sees "list of chunks → list of chunks"
  regardless of origin), and
* the deterministic vocabulary patterns the rule-based router matches against.

Keeping this knowledge in ONE registry is what lets the rest of the pipeline
(router, store, advanced pipeline) stay collection-agnostic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from sqlalchemy import Date, Integer, cast

from app.generation.rag.schemas import RetrievedChunk
from app.generation.rag.store.models import (
    BudgetChunkRow,
    TechnicalDocChunkRow,
    TranscriptChunkRow,
)


class Collection(StrEnum):
    """Closed set of retrievable collections. Used as the router's target enum
    (the LLM classifier can only emit these values) and as the registry key."""

    BUDGET = "budget"
    TRANSCRIPT = "transcript"
    TECHNICAL_DOC = "technical_doc"


@dataclass(frozen=True)
class HardFilters:
    """Metadata constraints applied BEFORE the vector ranking (Article 6).

    Each collection consumes the subset it understands; the rest is ignored.
    A ``None``/empty field means "do not constrain on this axis".
    """

    technologies: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    min_date: date | None = None  # budgets: year >= min_date.year; transcripts: meeting_date >=
    version: str | None = None  # technical docs only


@dataclass(frozen=True)
class CollectionSpec:
    """Everything the pipeline needs to treat a collection uniformly."""

    collection: Collection
    model: type
    chunk_type: str  # stamped on every chunk of this collection at ingest
    source_id_key: str  # JSONB key holding the parent document's traceable id
    date_key: str | None  # JSONB key holding the chunk's date (for temporal decay)
    date_kind: str | None  # "year" (int) | "iso" (YYYY-MM-DD string) | None
    rule_patterns: tuple[str, ...] = field(default_factory=tuple)

    def date_of(self, metadata: dict) -> date | None:
        """Read a :class:`date` from this collection's metadata, or ``None``."""
        if not self.date_key or self.date_kind is None:
            return None
        raw = (metadata or {}).get(self.date_key)
        if raw in (None, ""):
            return None
        try:
            if self.date_kind == "year":
                return date(int(raw), 1, 1)
            if self.date_kind == "iso":
                return date.fromisoformat(str(raw))
        except (ValueError, TypeError):
            return None
        return None

    def to_chunk(
        self, row, *, distance: float, relevance_score: float | None = None
    ) -> RetrievedChunk:
        """Map a DB row of this collection onto the uniform retrieval contract."""
        md = row.metadata_ or {}
        return RetrievedChunk(
            id=row.id,
            content=row.content,
            sector=str(md.get("client_sector", "unknown")),
            project_year=int(md.get("year", 0) or 0),
            chunk_type=row.chunk_type,
            distance=distance,
            budget_id=md.get("budget_id"),
            collection=self.collection.value,
            source_id=md.get(self.source_id_key),
            relevance_score=relevance_score,
            document_date=self.date_of(md),
            estimated_hours=md.get("estimated_hours"),
        )

    def hard_filter_clauses(self, filters: HardFilters) -> list:
        """SQLAlchemy predicates embedding ``filters`` into the search query.

        Only the axes this collection understands are emitted. Empty ⇒ no
        constraint, so the search behaves exactly as if no filter was passed.
        """
        model = self.model
        md = model.metadata_
        clauses: list = []

        if self.collection is Collection.BUDGET:
            if filters.sectors:
                clauses.append(md["client_sector"].astext.in_(list(filters.sectors)))
            if filters.technologies:
                clauses.append(md["main_technology"].astext.in_(list(filters.technologies)))
            if filters.min_date is not None:
                clauses.append(cast(md["year"].astext, Integer) >= filters.min_date.year)
        elif self.collection is Collection.TRANSCRIPT:
            if filters.min_date is not None:
                clauses.append(cast(md["meeting_date"].astext, Date) >= filters.min_date)
        elif self.collection is Collection.TECHNICAL_DOC:
            if filters.version is not None:
                clauses.append(md["version"].astext == filters.version)

        return clauses


# The registry. Order is the canonical "search everything" order (fallback).
COLLECTIONS: dict[Collection, CollectionSpec] = {
    Collection.BUDGET: CollectionSpec(
        collection=Collection.BUDGET,
        model=BudgetChunkRow,
        chunk_type="budget_component",
        source_id_key="budget_id",
        date_key="year",
        date_kind="year",
        rule_patterns=(
            r"\bbudget(s)?\b",
            r"\bquote(s|d)?\b",
            r"\bestimat(e|ed|ion|es)\b",
            r"\bcost(s|ed)?\b",
            r"\bhours?\b",
            r"\bhow much\b",
            r"\bprice(s|d)?\b",
        ),
    ),
    Collection.TRANSCRIPT: CollectionSpec(
        collection=Collection.TRANSCRIPT,
        model=TranscriptChunkRow,
        chunk_type="meeting_segment",
        source_id_key="transcript_id",
        date_key="meeting_date",
        date_kind="iso",
        rule_patterns=(
            r"\bmeeting(s)?\b",
            r"\btranscript(s)?\b",
            r"\bcall(s)?\b",
            r"\bdiscuss(ed|ion)?\b",
            r"\bclient (said|asked|wants?|mentioned)\b",
            r"\bkick-?off\b",
            r"\bstand-?up\b",
        ),
    ),
    Collection.TECHNICAL_DOC: CollectionSpec(
        collection=Collection.TECHNICAL_DOC,
        model=TechnicalDocChunkRow,
        chunk_type="doc_section",
        source_id_key="doc_id",
        date_key=None,
        date_kind=None,
        rule_patterns=(
            r"\bdocument(ation|s)?\b",
            r"\bspec(ification)?s?\b",
            r"\bAPI reference\b",
            r"\barchitecture\b",
            r"\bhow (do|to) (we|i) (implement|configure|integrate)\b",
            r"\brunbook\b",
            r"\bversion\b",
        ),
    ),
}

ALL_COLLECTIONS: tuple[Collection, ...] = tuple(COLLECTIONS)


def spec_for(collection: Collection) -> CollectionSpec:
    return COLLECTIONS[collection]


def match_rules(query_text: str) -> list[Collection]:
    """Deterministic vocabulary routing (cascade level 1).

    Returns every collection whose vocabulary appears in the query, in registry
    order. Empty ⇒ no rule fired (the cascade then escalates to the classifier).
    """
    text = query_text.lower()
    hits: list[Collection] = []
    for collection, spec in COLLECTIONS.items():
        if any(re.search(pattern, text) for pattern in spec.rule_patterns):
            hits.append(collection)
    return hits
