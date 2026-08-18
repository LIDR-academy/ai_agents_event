"""Request and response models for the estimation endpoint.

Session 4 contract: typed form-style request maps to a typed, validated
``EstimationResult`` (structured output via Instructor + Pydantic). Two model
validators enforce business rules that the LLM cannot break:

1. The cost of all phases must sum to ``total_cost_eur``.
2. Low-confidence answers (< 30%) must declare it explicitly by starting the
   summary with ``"Out of scope:"``.

When the LLM violates a validator, Instructor re-prompts the model with the
``ValueError`` message until it agrees (up to ``max_retries`` attempts).
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProjectType(str, Enum):
    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(str, Enum):
    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(str, Enum):
    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class EstimationRequest(BaseModel):
    """Typed payload sent by the business backend or Streamlit form."""

    description: str = Field(
        min_length=20,
        max_length=80000,
        description="Free-text description or transcription of the project to estimate.",
    )
    project_type: ProjectType = Field(description="Coarse-grained project category.")
    detail_level: DetailLevel = Field(description="How deep the estimation should go.")
    output_format: OutputFormat = Field(description="Shape of the rendered estimation.")


# --- Structured response ----------------------------------------------------


OUT_OF_SCOPE_PREFIX = "Out of scope:"
LOW_CONFIDENCE_THRESHOLD = 30


class Phase(BaseModel):
    """One phase in the breakdown of an estimation."""

    name: str = Field(min_length=1, max_length=64)
    duration_weeks: int = Field(ge=1, le=52)
    cost_eur: int = Field(ge=0, le=1_000_000)
    summary: str = Field(min_length=10, max_length=600)


class EstimationResult(BaseModel):
    """Structured estimation. The two validators below are the business rules
    that the LLM cannot break — Instructor will re-prompt the model when one
    of them raises.

    Field order is deliberate: ``phases`` comes BEFORE the totals so the LLM
    commits to the per-phase numbers first (autoregressive generation) and
    then only needs to sum them when filling the totals. Putting totals first
    leads the model to pick a round number and then back-fit phases to it,
    which it does very badly arithmetically — particularly with smaller
    models like ``gpt-4o-mini``.
    """

    summary: str = Field(min_length=10, max_length=1200)
    confidence_pct: int = Field(ge=0, le=100)
    phases: list[Phase] = Field(min_length=1, max_length=8)
    total_duration_weeks: int = Field(ge=1, le=104)
    total_cost_eur: int = Field(ge=0, le=2_000_000)

    @model_validator(mode="after")
    def phases_sum_matches_total(self) -> "EstimationResult":
        phase_sum = sum(p.cost_eur for p in self.phases)
        if phase_sum != self.total_cost_eur:
            raise ValueError(
                f"phases sum ({phase_sum} EUR) does not match total_cost_eur "
                f"({self.total_cost_eur} EUR); adjust either the phases or the total"
            )
        return self

    @model_validator(mode="after")
    def low_confidence_requires_out_of_scope_prefix(self) -> "EstimationResult":
        if self.confidence_pct < LOW_CONFIDENCE_THRESHOLD and not self.summary.startswith(
            OUT_OF_SCOPE_PREFIX
        ):
            raise ValueError(
                f"confidence_pct < {LOW_CONFIDENCE_THRESHOLD} requires summary to "
                f"start with {OUT_OF_SCOPE_PREFIX!r}; refuse the estimation if the "
                f"description is too vague to size"
            )
        return self


class TurnObservation(BaseModel):
    """Per-turn telemetry attached to a conversational response.

    Populated by ``estimate_conversational`` only; the non-conversational
    endpoint leaves ``EstimationResponse.observation`` as ``None``. The stress
    runner reads this field directly from the JSON response — no log parsing.

    ``cache_hit_kind`` is always ``"none"`` for the conversational path
    (sessions bypass both caches by design); the field is kept for symmetry
    with the non-conversational endpoint and to document that choice.
    """

    turn_index: int = Field(ge=1)
    session_id: str
    enriched_transcript_chars: int = Field(ge=0)
    attachments_total_chars: int = Field(ge=0)
    messages_in_window: int = Field(ge=0)
    anchors_count: int = Field(ge=0)
    summary_chars: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cache_hit_kind: Literal["none", "exact", "semantic"] = "none"
    last_resolved_tier: str | None = None


class EstimationResponse(BaseModel):
    """Wraps the validated result, the prompt version that produced it, and
    whether it came from a cache (exact or semantic).

    ``observation`` is populated only by the conversational endpoint
    (``POST /sessions/{id}/estimate``). It carries the per-turn telemetry the
    stress runner needs without contaminating the production contract — older
    callers that ignore the field keep working.
    """

    result: EstimationResult
    prompt_version: str
    cached: bool = False
    observation: TurnObservation | None = None


from app.domain.schemas.acb import BossTrace  # noqa: E402


class ACBResponse(EstimationResponse):
    """Conversational response with the Actor-Critic-Boss audit trail.

    Same shape as ``EstimationResponse`` plus the ``acb`` field carrying the
    iteration log. The UI uses the trail to render an expander showing what
    the Critic flagged at each step and how the Boss decided.
    """

    acb: BossTrace
