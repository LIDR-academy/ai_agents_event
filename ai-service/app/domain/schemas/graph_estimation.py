"""The public contract for the graph estimate endpoint (Session 13).

These mirror the "transcript in, structured estimate + status out" contract the
service has always exposed — the LangGraph machinery underneath is invisible to the
Rails business backend. Kept in ``domain/schemas`` (the contract layer), separate
from the node-internal LLM models in ``app/domain/graph/schemas.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.graph.state import BudgetMatch, Component


class GraphEstimateRequest(BaseModel):
    """Payload for ``POST /v1/estimate/graph``."""

    transcript: str = Field(min_length=100, max_length=50_000)
    # Used as the checkpointer ``thread_id`` so a re-run resumes the same thread.
    # Defaults to a fresh UUID in the router when omitted.
    estimation_id: str | None = Field(default=None, max_length=128)


class GraphEstimateResponse(BaseModel):
    """The graph's terminal state, surfaced as the endpoint response.

    ``estimate`` is the consolidated ``ConsolidatedEstimate`` (as a dict) and
    ``status`` is the value the ``validate_and_consolidate`` node set — the two the
    external contract cares about. The rest expose the intermediate artifacts for
    the wizard / debugging.
    """

    estimation_id: str
    status: str  # "validated" | "needs_review"
    estimate: dict | None = None
    requirements: list[str] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    budget_matches: list[BudgetMatch] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Session 13 (live) — the multi-agent flow's start / resume / state contract  #
# --------------------------------------------------------------------------- #
class GraphResumeRequest(BaseModel):
    """Payload for ``POST /v1/estimate/graph/{estimation_id}/resume``.

    ``decision`` is the human's answer to whichever gate the run is paused at. Its
    shape depends on the gate (the ``pending_gate.gate`` tells the client which):

    * ``structure_review`` → ``{"approved": bool, "modules": [ {name, tasks:[{name,
      description}]} ]}`` (the human-edited module→task tree; omit ``modules`` to
      accept the structure as proposed).
    * ``final_review`` → ``{"validated": bool, "estimate_overrides": {...},
      "want_proposal": bool}``.

    Kept as a free-form dict on purpose: the service IA exposes ONE resume verb and
    the business backend supplies the gate-appropriate decision. Any HTTP client can
    drive it — the pattern is stack-agnostic.
    """

    decision: dict = Field(default_factory=dict)


class PendingGate(BaseModel):
    """The human gate a paused run is waiting on (the ``interrupt`` payload)."""

    gate: str  # "structure_review" | "final_review"
    estimation_id: str
    # The artifacts the human reviews at this gate (structure / estimate + report).
    payload: dict = Field(default_factory=dict)


class GraphRunState(BaseModel):
    """A snapshot of the multi-agent run: either paused at a gate, or completed.

    Returned by START (``POST /graph``), RESUME (``.../resume``) and the read-only
    STATE endpoint (``GET .../state``). When ``state == "paused"`` the client renders
    ``pending_gate``; when ``"completed"`` it renders ``estimate`` (+ ``proposal``).
    """

    estimation_id: str
    state: str  # "paused" | "completed"
    pending_gate: PendingGate | None = None
    complexity: str | None = None
    structure: dict | None = None
    task_hours: list[dict] = Field(default_factory=list)
    estimate: dict | None = None
    analysis_report: dict | None = None
    proposal: str | None = None
    status: str | None = None  # "validated" | "needs_review" (set at gate 2)
    errors: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Session 13 (live) — the "watch the agents work" live panel contract         #
# --------------------------------------------------------------------------- #
class ActivityEntry(BaseModel):
    """One didactic line of what an agent just did (see ``graph/activity.py``)."""

    seq: int = 0
    node: str  # stable key aligned with the Rails Agents::GraphFlow node keys
    label: str  # short human label (e.g. "Classifier")
    message: str  # the didactic one-liner (e.g. "Complejidad: high")
    ts: str | None = None


class GraphProgress(GraphRunState):
    """Live progress of a background-streamed run: ``GraphRunState`` + the activity feed.

    ``state`` gains a third value, ``"running"`` — the run is mid-leg (executing between
    two gates), not yet paused nor completed. The wizard polls this while the panel fills
    in, then reloads once ``state`` becomes ``"paused"`` or ``"completed"``.
    """

    state: Literal["running", "paused", "completed"]  # type: ignore[assignment]
    activity: list[ActivityEntry] = Field(default_factory=list)


class GraphProposalRequest(BaseModel):
    """Optional body for ``POST …/graph/{id}/proposal``.

    The business backend owns the money, so it may hand over the CURRENT pricing instead of
    letting the service reuse whatever was frozen into the graph state at gate 2. Without
    this, re-drafting a proposal after changing the rate would quote the old figure.
    """

    pricing: dict | None = Field(
        default=None, description="Pricing block to quote; falls back to the run's state."
    )


class GraphProposalResponse(BaseModel):
    """The full commercial proposal drafted by ``POST …/graph/{id}/proposal``.

    Generated on demand over the run's already-validated estimate (no graph re-run),
    so the wizard can produce/regenerate the proposal after completion even when it
    was not requested at gate 2. Mirrors the node-internal ``CommercialProposal``.
    """

    estimation_id: str
    title: str
    executive_summary: str
    scope: list[str] = Field(default_factory=list)
    total_engineer_days: int | None = None
    # The price the model QUOTED. The caller computed it in the first place, so this is a
    # check, not a source: a mismatch means the agent derived its own figure.
    total_price_eur: int | None = None
    body_markdown: str
