"""Pydantic models for the embedding pipeline.

Input side mirrors the normalized historical-budget JSON (a budget with a list
of components). Output side carries chunks ready to embed and, once embedded,
the vectors plus aggregate stats.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.schemas.agent_trace import AgentTrace

# Closed universe of client sectors present in the sample dataset. Kept as a
# Literal so a typo or an unexpected sector fails validation loudly instead of
# silently leaking into the metadata.
Sector = Literal[
    "finance",
    "ecommerce",
    "healthcare",
    "industrial",
    "logistics",
    "education",
    "media",
    "government",
]
Complexity = Literal["low", "medium", "high"]


class ClientMetadata(BaseModel):
    """Who the budget belongs to. Travels as filterable context, not embedded."""

    name: str = Field(description="Client company name.")
    sector: Sector = Field(description="Client business sector.")
    country: str = Field(description="ISO-ish country code, e.g. 'ES'.")


class BudgetComponent(BaseModel):
    """A single line item of a historical budget."""

    component_id: str = Field(description="Stable id within the budget, e.g. 'AUTH-001'.")
    name: str = Field(description="Short human-readable component name.")
    description: str = Field(description="Detailed description of the work.")
    module: str | None = Field(
        default=None,
        description="Functional block this component/task belongs to (e.g. 'Payments').",
    )
    tech_stack: list[str] = Field(
        default_factory=list, description="Technologies involved in this component."
    )
    estimated_hours: int = Field(ge=0, description="Hours estimated for this component.")
    complexity: Complexity = Field(description="Coarse complexity bucket.")
    dependencies: list[str] = Field(
        default_factory=list, description="component_ids this one depends on."
    )


class Budget(BaseModel):
    """A complete historical budget with its components."""

    budget_id: str = Field(description="Stable budget id, e.g. 'BUD-2024-014'.")
    client_metadata: ClientMetadata
    project_summary: str = Field(description="One-line summary of the project.")
    main_technology: str = Field(description="Primary technology / stack of the project.")
    year: int = Field(ge=2000, le=2100, description="Year the budget was produced.")
    total_estimated_hours: int = Field(ge=0, description="Sum of component hours, as recorded.")
    components: list[BudgetComponent] = Field(min_length=1, description="Budget line items.")


class Chunk(BaseModel):
    """A fragment ready to be embedded.

    ``text`` is what gets sent to the embeddings API; ``metadata`` carries
    filterable fields that travel alongside the chunk but are NOT embedded.
    """

    chunk_id: str = Field(description="Traceable id, format '{budget_id}::{component_id}'.")
    text: str = Field(description="Embeddable text: parent context + component detail.")
    metadata: dict = Field(default_factory=dict, description="Filterable, non-embedded fields.")
    token_count: int = Field(ge=0, description="Token count of ``text`` (tiktoken).")


class EmbeddedChunk(Chunk):
    """A :class:`Chunk` with its embedding vector attached."""

    embedding: list[float] = Field(
        description="Dense embedding vector (1536 dims for text-embedding-3-small)."
    )


class IngestRequest(BaseModel):
    """Payload for ``POST /embeddings/ingest`` (Session 8: persisting contract).

    One request = one document. ``content`` is the full budget JSON, validated
    against :class:`Budget` so a malformed corpus fails with a 422 before
    touching the database or the embeddings API.
    """

    source_path: str = Field(
        min_length=1, description="Provenance of the document, unique per ingest."
    )
    document_type: str = Field(
        min_length=1, max_length=50, description="Document family, e.g. 'historical_budget'."
    )
    content: Budget = Field(description="Full budget JSON, as produced upstream.")
    chunk_type: str = Field(
        default="budget_component",
        max_length=50,
        description="chunk_type stamped on every chunk (filterable). Defaults keep S08 behaviour; "
        "the task corpus uses 'historical_task'.",
    )


class IngestResponse(BaseModel):
    """Response for ``POST /embeddings/ingest``: identifiers + ingest metrics.

    Vectors no longer travel over HTTP — they are persisted in pgvector.
    """

    document_id: int = Field(description="Primary key of the persisted document.")
    chunks_created: int = Field(ge=0, description="Chunks persisted for this document.")
    embedding_dimension: int = Field(description="Dimensionality of the stored vectors.")
    ingestion_time_ms: int = Field(ge=0, description="Wall-clock ingest time.")


# ---------------------------------------------------------------------------
# Session 11 — incremental corpus expansion (add NEW information + index it).
#
# Not re-embedding the existing corpus (that would need a model change / a
# blue-green migration, out of scope): this is growing the vector DB with new
# documents, embedded with the SAME model, indexed by the HNSW index the
# Session 11 migration provisions. Runs as an async job (BackgroundTask) so a
# batch of documents can report progress instead of blocking the request.
# ---------------------------------------------------------------------------


class IndexRunRequest(BaseModel):
    """Payload for ``POST /embeddings/index/runs``: a batch of new documents."""

    documents: list[Budget] = Field(
        min_length=1, max_length=200, description="New budget documents to add to the corpus."
    )
    document_type: str = Field(
        default="historical_budget",
        min_length=1,
        max_length=50,
        description="Document family stamped on every new document.",
    )
    chunk_type: str = Field(
        default="budget_component",
        max_length=50,
        description="chunk_type stamped on every new chunk (filterable).",
    )


class IndexRunResponse(BaseModel):
    """Response of ``POST /embeddings/index/runs``. Returned with HTTP 202."""

    job_id: uuid.UUID
    documents_total: int = Field(ge=0, description="Documents submitted for indexing.")
    status: Literal["pending", "running", "completed", "failed"]


class IndexJobView(BaseModel):
    """Response of ``GET /embeddings/index/jobs/{job_id}``: progress snapshot."""

    job_id: uuid.UUID
    status: Literal["pending", "running", "completed", "failed"]
    documents_processed: int = Field(
        ge=0, description="Documents processed so far (indexed + skipped duplicates)."
    )
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class CollectionStats(BaseModel):
    """Corpus size + index state for one collection table."""

    collection: str
    documents: int = Field(ge=0, description="Distinct documents with chunks in this collection.")
    chunks: int = Field(ge=0, description="Chunk rows in this collection.")
    hnsw_indexed: bool = Field(description="Whether the collection carries an HNSW vector index.")


class CorpusStats(BaseModel):
    """Whole-corpus snapshot, per collection — surfaced so the UI shows growth."""

    collections: list[CollectionStats] = Field(default_factory=list)
    total_chunks: int = Field(ge=0)


class SearchRequest(BaseModel):
    """Payload for ``POST /search``."""

    query: str = Field(min_length=1, description="Free-text semantic query.")
    k: int = Field(default=5, ge=1, le=50, description="Number of nearest chunks to return.")


class SearchHit(BaseModel):
    """One ranked chunk. ``chunk_id`` is the DB primary key; the traceable
    corpus id ('BUD-X::COMP-Y' parts) travels inside ``metadata``."""

    chunk_id: int
    document_id: int
    chunk_type: str
    content: str
    distance: float = Field(description="Cosine distance (lower = more similar).")
    metadata: dict


class SearchResponse(BaseModel):
    """Response for ``POST /search``."""

    query: str
    k: int
    search_time_ms: int = Field(ge=0)
    results: list[SearchHit]


# ---------------------------------------------------------------------------
# Session 9 — RAG estimation pipeline (query understanding → generation).
#
# These types implement the locked contract from the Session 9 articles. They
# live alongside the Session 8 search types above; nothing here replaces them.
# ---------------------------------------------------------------------------

Scale = Literal["small", "medium", "large", "unknown"]
Confidence = Literal["high", "medium", "low", "insufficient"]
Relevance = Literal["primary", "supporting", "tangential"]
Impact = Literal["high", "medium", "low"]


class EstimationQuery(BaseModel):
    """Structured brief distilled from a raw meeting transcript.

    This is the output of the query-understanding stage: a transcript is full of
    digressions, so we extract only what drives retrieval (what to build, with
    which tech, under which constraints) instead of embedding the raw text.
    """

    function: str = Field(description="Functional summary of the project.")
    technologies: list[str] = Field(default_factory=list)
    sector: str | None = None
    scale: Scale = "unknown"
    country: str | None = None
    regulations: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class RetrievedChunk(BaseModel):
    """One chunk returned by the metadata-filtered retriever.

    ``id`` is the chunk's DB primary key (cited as a ``source id`` downstream).
    ``sector``/``project_year`` are flattened from the chunk's JSONB metadata
    (``client_sector``/``year``) so the generator and the citation validator see
    a stable, typed shape.
    """

    id: int
    content: str
    # sector/project_year are budget-centric; they default for the Session 10
    # collections (transcripts, technical docs) that carry no such metadata.
    sector: str = "unknown"
    project_year: int = 0
    chunk_type: str
    distance: float = Field(description="Cosine distance (lower = more similar).")
    budget_id: str | None = Field(
        default=None,
        description="Traceable corpus id of the parent budget (from JSONB metadata). "
        "Used by the Session 10 retrieval evaluation to grade precision per budget.",
    )
    # --- Session 10 multi-index fields (default-valued → backward compatible) ---
    collection: str = Field(
        default="budget",
        description="Provenance label: which collection this chunk came from "
        "(budget / transcript / technical_doc). Travels with the chunk for "
        "attribution, auditing and debugging (Article 5).",
    )
    source_id: str | None = Field(
        default=None,
        description="Generic traceable id of the parent document, whatever the "
        "collection (budget_id, transcript_id, doc_id). Used to grade precision.",
    )
    relevance_score: float | None = Field(
        default=None,
        description="Final relevance score after reranking / fusion, before/after "
        "temporal decay. Higher = better. None on the plain vector path.",
    )
    document_date: date | None = Field(
        default=None,
        description="Date used by temporal decay (budget year-start or transcript "
        "meeting date). None when the collection carries no usable date.",
    )
    estimated_hours: int | None = Field(
        default=None,
        description="Historical hours recorded for this chunk (budget component / "
        "historical task), flattened from JSONB metadata. None for collections that "
        "carry no hours. Read by the per-task hours estimator (Session 10).",
    )


class RetrievalResult(BaseModel):
    """Outcome of the retrieval stage."""

    chunks: list[RetrievedChunk]
    low_confidence: bool = Field(
        description="True when no chunk crossed the distance threshold (soft-fail)."
    )
    candidates_evaluated: int = Field(
        ge=0, description="Total chunks scored before applying the threshold/limit."
    )


class SourceCitation(BaseModel):
    """A reference from the estimate back to a retrieved chunk."""

    source_id: int = Field(description="DB id of the cited chunk (a RetrievedChunk.id).")
    relevance: Relevance
    used_for: str = Field(description="What this source contributed to the estimate.")


class Assumption(BaseModel):
    """An estimate component NOT backed by any retrieved source."""

    description: str
    impact: Impact
    rationale: str


class SourceReference(BaseModel):
    """A verifiable, line-level citation back to a single retrieved chunk.

    Session 11 lifts citation from the coarse, estimate-global
    :class:`SourceCitation` down to the individual estimate line: every grounded
    line carries the concrete chunk it derived from plus the verbatim evidence
    that backs it, so a reader (and :func:`validation.verify_citations`) can
    check the claim instead of trusting it.
    """

    chunk_id: str = Field(
        description="Id of the retrieved chunk supporting this line — the `id` "
        "attribute of the <source> element it was taken from."
    )
    document_id: str = Field(
        description="Historical budget document the chunk belongs to — the "
        "`document_id` attribute of the <source> element."
    )
    evidence: str = Field(
        description="Verbatim span or figure copied from the source that backs "
        "this line. Quote the source; do not paraphrase."
    )


class TaskItem(BaseModel):
    """One concrete engineering task inside a functional module, in engineer-days.

    Each task is an estimate *line*. When ``grounded`` is True the line derives
    from historical evidence and carries at least one :class:`SourceReference`;
    when False the line has no sufficient source data — it must not invent hours
    and should surface its scope as an :class:`Assumption` instead.
    """

    name: str
    description: str | None = Field(default=None, description="One-line scope of the task.")
    engineer_days: int | None = Field(
        default=None,
        ge=0,
        description="Effort in engineer-days. None in the structure-only generation "
        "mode (Session 10): the LLM proposes the module→task structure and the hours "
        "are derived afterwards by per-task vector search, not inferred here. Must be "
        "None when ``grounded`` is False (no source data => no invented hours).",
    )
    grounded: bool = Field(
        default=False,
        description="True iff this line is backed by retrieved source data. False "
        "means 'no sufficient source data': the line carries no sources and no "
        "invented hours.",
    )
    sources: list[SourceReference] = Field(
        default_factory=list,
        description="Per-line citations. Non-empty iff ``grounded`` is True.",
    )

    @model_validator(mode="after")
    def _grounding_integrity(self) -> "TaskItem":
        """Enforce the Session 11 line-level grounding contract.

        A grounded line must cite at least one source; an ungrounded line must
        neither cite sources nor invent hours. Violations raise so Instructor
        re-prompts the model rather than letting an unverifiable line through.
        """
        if self.grounded and not self.sources:
            raise ValueError("a grounded line must cite at least one source")
        if not self.grounded and self.sources:
            raise ValueError("an ungrounded line (grounded=False) must not carry sources")
        if not self.grounded and self.engineer_days is not None:
            raise ValueError(
                "an ungrounded line (grounded=False) must leave engineer_days null; "
                "do not invent hours without source data"
            )
        return self


class WorkModule(BaseModel):
    """A functional block (e.g. Auth, Payments, Data, Frontend, Infra, QA, PM)
    grouping the concrete tasks needed to deliver it."""

    name: str
    description: str | None = Field(default=None, description="What this functional block covers.")
    tasks: list[TaskItem] = Field(default_factory=list)


class Estimate(BaseModel):
    """Grounded estimate produced from retrieved historical budgets.

    Hours-based (engineer-days) with mandatory citations — distinct from the
    Session 4 ``EstimationResult`` (euros/weeks/phases). The breakdown is
    organised into functional modules, each decomposed into concrete tasks.
    When the retrieved context is insufficient, ``confidence='insufficient'``
    and the numeric totals stay ``None`` with ``modules`` empty (enforced by
    :func:`validation.check_coherence`).
    """

    total_engineer_days: int | None = None
    modules: list[WorkModule] = Field(default_factory=list)
    duration_weeks: int | None = None
    sources: list[SourceCitation] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    confidence: Confidence
    reasoning: str = Field(description="How the estimate was derived from the sources.")
    insufficient_context_explanation: str | None = None


# ---------------------------------------------------------------------------
# Session 11 — programmatic citation verification.
#
# The output of :func:`validation.verify_citations`: a per-line audit of where
# every estimate line's citation points and whether that chunk was actually in
# the retrieved context. A dangling citation (an id the LLM never saw) is a
# grounding failure, not a cosmetic detail — the report keeps it visible.
# ---------------------------------------------------------------------------

LineCitationStatus = Literal["grounded", "dangling", "insufficient"]


class LineCitation(BaseModel):
    """The citation status of a single estimate line."""

    module: str = Field(description="Module the line belongs to.")
    component: str = Field(description="The line (task) name.")
    status: LineCitationStatus = Field(
        description="grounded = all cited chunks were retrieved; dangling = at "
        "least one cited chunk_id was never in the context; insufficient = the "
        "line is flagged as having no sufficient source data."
    )
    cited_chunk_ids: list[str] = Field(default_factory=list)
    dangling_chunk_ids: list[str] = Field(
        default_factory=list, description="Cited ids that were never retrieved."
    )


class CitationReport(BaseModel):
    """Aggregate result of verifying an estimate's citations against the context."""

    total_lines: int = Field(ge=0)
    grounded_lines: int = Field(ge=0, description="Lines whose every citation was retrieved.")
    dangling_lines: int = Field(ge=0, description="Lines with at least one fabricated citation.")
    insufficient_lines: int = Field(ge=0, description="Lines flagged as no sufficient data.")
    verified_citations: int = Field(
        ge=0, description="Count of individual citations that resolved to a retrieved chunk."
    )
    dangling_citations: list[str] = Field(
        default_factory=list,
        description="Sorted, de-duplicated cited ids that were never retrieved "
        "(line-level and estimate-global). Empty means every citation is real.",
    )
    lines: list[LineCitation] = Field(default_factory=list)

    @property
    def has_dangling(self) -> bool:
        """True when any citation points outside the retrieved context."""
        return bool(self.dangling_citations)


# ---------------------------------------------------------------------------
# Session 11 — semantic hallucination gate.
#
# verify_citations proves REFERENTIAL integrity (the cited chunk was retrieved).
# It cannot prove the number is ENTAILED by that chunk: a line can cite a real
# source and still invent its hours. The gate adds the semantic layer — a
# deterministic numeric anchor + a strict judge — grading each grounded line
# grounded / insufficient / degraded.
# ---------------------------------------------------------------------------


class LineVerdict(BaseModel):
    """Strict-judge verdict for a single estimate line."""

    module: str = Field(description="Module the line belongs to (echoed for matching).")
    component: str = Field(description="The line (task) name (echoed for matching).")
    entailed: bool = Field(
        description="True iff the cited evidence supports the line's hours/scope."
    )
    reason: str = Field(description="One-sentence justification for the verdict.")


LineGateStatus = Literal["grounded", "degraded", "insufficient"]


class LineGate(BaseModel):
    """Graded semantic-gate result for one estimate line."""

    module: str
    component: str
    status: LineGateStatus = Field(
        description="grounded = anchor and judge both accept the line; degraded = a "
        "grounded line whose hours are not entailed by the cited evidence (numeric "
        "anchor miss or judge rejection); insufficient = the line carried no data."
    )
    numeric_deviation: float | None = Field(
        default=None,
        ge=0.0,
        description="Relative gap between the line's hours and the deterministic anchor "
        "(|line - anchor| / anchor). None when no numeric anchor was available.",
    )
    reason: str = Field(default="", description="Why the line was degraded (empty when grounded).")


class HallucinationReport(BaseModel):
    """Aggregate of the semantic gate over an estimate's lines."""

    total_lines: int = Field(ge=0)
    grounded_lines: int = Field(ge=0, description="Lines the anchor and judge both accept.")
    degraded_lines: int = Field(
        ge=0, description="Lines that cite a real source but whose number it does not entail."
    )
    insufficient_lines: int = Field(ge=0, description="Lines flagged as no sufficient data.")
    lines: list[LineGate] = Field(default_factory=list)

    @property
    def has_degraded(self) -> bool:
        """True when any grounded line failed the semantic check."""
        return self.degraded_lines > 0


# ---- HTTP request models for the Session 9 routers ------------------------
# Named ``RetrievalRequest``/``EstimateRequest`` (not ``SearchRequest``) to
# avoid colliding with the Session 8 ``SearchRequest`` above.


class RetrievalRequest(BaseModel):
    """Payload for ``POST /v1/retrieval/search`` (threshold + structural filters)."""

    query_text: str = Field(min_length=10, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=30)
    distance_threshold: float = Field(default=0.6, ge=0.0, le=2.0)
    sectors: list[str] | None = None
    project_year_min: int | None = Field(default=None, ge=2010, le=2100)
    project_year_max: int | None = Field(default=None, ge=2010, le=2100)
    chunk_types: list[str] | None = None
    # Session 10 overrides (None = fall back to runtime/settings default). These
    # make the four measurement configurations invocable per request.
    search_mode: Literal["vector", "hybrid"] | None = None
    rerank: bool | None = None


class EstimateRequest(BaseModel):
    """Payload for ``POST /v1/estimate/from-transcript``."""

    transcript: str = Field(min_length=100, max_length=50_000)
    idempotency_key: str | None = Field(default=None, max_length=128)


# ---- Per-stage request/response models for the wizard (S09 teaching aid) ---
# The full pipeline (``estimate_from_transcript``) hides its intermediate
# artifacts; these stateless stage endpoints expose each step so a UI can run
# the pipeline one stage at a time. They REUSE the pure functions in this
# package — they do not re-implement any pipeline logic. Retrieval reuses
# ``RetrievalRequest``/``RetrievalResult`` above (zero new schema).


class ReformulateRequest(BaseModel):
    """Payload for ``POST /v1/estimate/stages/reformulate``."""

    transcript: str = Field(min_length=100, max_length=50_000)


class ReformulationResult(BaseModel):
    """Output of the query-understanding stage: the structured brief plus the
    canonical search text that gets embedded for retrieval."""

    query: EstimationQuery
    search_text: str = Field(description="Corpus-aligned text fed to the embedder.")


class AssembleRequest(BaseModel):
    """Payload for ``POST /v1/estimate/stages/assemble``.

    ``max_context_tokens`` defaults (server-side) to ``MAX_CONTEXT_TOKENS`` when
    omitted; a small value lets a demo show whole-chunk truncation."""

    chunks: list[RetrievedChunk]
    max_context_tokens: int | None = Field(default=None, ge=256, le=64_000)
    # Session 11 augmentation toggles (per request, so a demo can show the effect).
    augment: bool = Field(
        default=False,
        description="Apply the Session 11 augmentation passes before assembly.",
    )
    compress: bool = Field(default=True, description="Compress each source to its key points.")
    reorder: bool = Field(default=True, description="Edge-load reorder (lost-in-the-middle).")


class AssembleResult(BaseModel):
    """Output of the augmentation stage: the assembled ``<source>`` block plus
    what survived the token budget."""

    context_block: str
    kept_chunks: list[RetrievedChunk]
    dropped_count: int = Field(ge=0, description="Chunks dropped by the token budget.")
    token_count: int = Field(ge=0, description="Tokens in the assembled context block.")
    augmented: bool = Field(
        default=False, description="Whether the Session 11 augmentation passes ran."
    )


class StructureRequest(BaseModel):
    """Payload for ``POST /v1/estimate/stages/structure`` (Session 10).

    The wizard generates the module→task structure as a free decomposition of the
    reformulated brief — no retrieval, no sources. Only the query is needed."""

    query: EstimationQuery


class GenerateRequest(BaseModel):
    """Payload for ``POST /v1/estimate/stages/generate``.

    ``kept_chunks`` are the chunks the context block was built from; they are
    used to validate citations (no fabricated source ids) after generation."""

    context_block: str = Field(min_length=1)
    query: EstimationQuery
    kept_chunks: list[RetrievedChunk] = Field(default_factory=list)
    include_hours: bool = Field(
        default=True,
        description="When False (Session 10), generate the module→task structure "
        "WITHOUT hours; the hours are derived later by per-task vector search. "
        "Defaults to True to preserve the Session 9 single-shot behaviour.",
    )


class GenerateResult(BaseModel):
    """Output of the generation stage: the estimate plus the grounding signals
    the wizard surfaces (instead of auto-retrying like the full pipeline)."""

    estimate: Estimate
    fabricated_source_ids: list[str] = Field(
        default_factory=list,
        description="Cited chunk_ids not present in kept_chunks (empty = clean). "
        "Mirrors ``citation_report.dangling_citations`` for backward compatibility.",
    )
    citation_report: CitationReport | None = Field(
        default=None,
        description="Session 11 per-line citation verification report (None for the "
        "structure-only stage, which has no sources to verify).",
    )
    coherent: bool = Field(description="False when an insufficient estimate still carries numbers.")
    agent_trace: AgentTrace | None = Field(
        default=None,
        description="Session 12: the hand-written agent's reason→act→observe trace when the "
        "structure was proposed by the agent (None on the deterministic /stages/structure path).",
    )


class VerifyRequest(BaseModel):
    """Payload for ``POST /v1/estimate/stages/verify`` (Session 11 semantic gate).

    Takes a generated estimate and the chunks its context was built from, and
    runs the deterministic anchor + strict judge over every grounded line."""

    estimate: Estimate
    kept_chunks: list[RetrievedChunk] = Field(default_factory=list)
    use_judge: bool = Field(
        default=True,
        description="When False, only the deterministic numeric anchor runs (no LLM) — "
        "useful for a fast, free demo of the gate's arithmetic half.",
    )


# ---------------------------------------------------------------------------
# Session 10 — per-task hours estimation by vector search.
#
# The structure-only generation produces modules → tasks WITHOUT hours. Each
# task is then matched against the historical task corpus (chunk_type
# ``historical_task``); the hours come from a weighted consensus of the nearest
# neighbours, with a reliability score. A task with no neighbour under the
# distance threshold gets no hours (surfaced as a red flag in the UI).
# ---------------------------------------------------------------------------


class TaskNeighbor(BaseModel):
    """One historical task that matched the query task, for transparency."""

    source_id: int = Field(description="DB id of the matched historical_task chunk.")
    budget_id: str | None = Field(default=None, description="Traceable parent corpus id.")
    estimated_hours: int = Field(ge=0, description="Hours recorded for this historical task.")
    distance: float = Field(description="Cosine distance to the query task (lower = closer).")


class HourRange(BaseModel):
    """A synthesized hour RANGE emitted when the historical sources contradict.

    Session 11: a single weighted consensus hides disagreement (one source says
    40h, another 90h → a misleading 65h point). When the neighbour dispersion
    crosses the contradiction threshold, synthesis surfaces the spread as a range
    plus the reason, instead of averaging the conflict away.
    """

    low: int = Field(ge=0, description="Low end of the plausible hours.")
    high: int = Field(ge=0, description="High end of the plausible hours.")
    reason: str = Field(description="Why the sources disagree (the conflict, named).")


class TaskHoursEstimate(BaseModel):
    """Hours derived for one task from the historical task corpus.

    ``has_match=False`` (no neighbour under the threshold) leaves
    ``estimated_hours``/``reliability`` null — the UI flags the row red and the
    human supplies the number in the validation step.
    """

    module: str = Field(description="Module the task belongs to (echoed back).")
    task: str = Field(description="Task name (echoed back).")
    estimated_hours: int | None = Field(
        default=None, ge=0, description="Weighted-consensus hours, or null when no match."
    )
    reliability: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="0..1 confidence in the hours (closeness × neighbour agreement).",
    )
    has_match: bool = Field(description="Whether any neighbour crossed the distance threshold.")
    dispersion: float | None = Field(
        default=None,
        ge=0.0,
        description="Spread of neighbour hours (coefficient of variation); higher = less agreement.",
    )
    neighbors: list[TaskNeighbor] = Field(
        default_factory=list, description="The historical tasks the hours were derived from."
    )
    # Session 11: when the neighbours contradict beyond the dispersion threshold,
    # synthesis surfaces the spread as a range with a reason instead of a point.
    hours_range: HourRange | None = Field(
        default=None,
        description="Present only when the historical sources contradict; the point "
        "``estimated_hours`` still carries the consensus for editing.",
    )


class TaskHoursTaskInput(BaseModel):
    """One task to estimate (name + optional description)."""

    name: str = Field(min_length=1)
    description: str | None = None


class TaskHoursModuleInput(BaseModel):
    """One module with its tasks, as reviewed by the human before hours."""

    name: str = Field(min_length=1)
    tasks: list[TaskHoursTaskInput] = Field(default_factory=list)


class TaskHoursRequest(BaseModel):
    """Payload for ``POST /v1/estimate/tasks/hours``."""

    modules: list[TaskHoursModuleInput] = Field(min_length=1)


class TaskHoursResult(BaseModel):
    """Per-task hours estimates, in the order the tasks were submitted."""

    tasks: list[TaskHoursEstimate] = Field(default_factory=list)
    agent_trace: AgentTrace | None = Field(
        default=None,
        description="Session 12: the hand-written agent's recovery trace when the agent re-searched "
        "the tasks the deterministic pass could not ground (None on /tasks/hours; empty-step trace "
        "when nothing needed recovery).",
    )


# ---------------------------------------------------------------------------
# Session 12 — request models for the agent-driven wizard phases.
#
# The agent drives the SAME two wizard steps as the deterministic path, so its
# endpoints reuse the deterministic response models (``GenerateResult`` /
# ``TaskHoursResult``). Only the request grows: the same body plus optional
# per-run agent knobs (an agent profile in the Rails UI), each falling back to
# the ``AGENT_*`` settings when null.
# ---------------------------------------------------------------------------

AgentReasoningEffort = Literal["minimal", "low", "medium", "high"]


class AgentStructureRequest(BaseModel):
    """Payload for ``POST /v1/estimate/agent/structure`` (phase 1)."""

    query: EstimationQuery
    model: str | None = Field(default=None, description="Override AGENT_MODEL for this run.")
    reasoning_effort: AgentReasoningEffort | None = Field(
        default=None, description="Override AGENT_REASONING_EFFORT for this run."
    )
    persona: str | None = Field(
        default=None,
        max_length=2000,
        description="Extra operator instructions appended to the agent system prompt.",
    )


class AgentHoursRequest(BaseModel):
    """Payload for ``POST /v1/estimate/agent/hours`` (phase 2, hybrid)."""

    modules: list[TaskHoursModuleInput] = Field(min_length=1)
    model: str | None = Field(default=None, description="Override AGENT_MODEL for this run.")
    reasoning_effort: AgentReasoningEffort | None = Field(
        default=None, description="Override AGENT_REASONING_EFFORT for this run."
    )
    max_iterations: int | None = Field(
        default=None, ge=1, le=20, description="Override AGENT_MAX_ITERATIONS (recovery safeguard)."
    )
    search_top_k: int | None = Field(
        default=None, ge=1, le=30, description="Neighbours per search (else runtime/settings default)."
    )
    search_distance_threshold: float | None = Field(
        default=None, ge=0.0, le=2.0, description="Cosine cutoff (else runtime/settings default)."
    )
    persona: str | None = Field(
        default=None,
        max_length=2000,
        description="Extra operator instructions appended to the agent system prompt.",
    )
