"""HTTP contract for the Session 14 supervisor flow.

Its own module rather than more rows in ``graph_estimation.py``: the two flows are
independent surfaces that happen to share a checkpointer, and keeping their contracts
apart is what lets either change without the other's clients noticing.

The external promise is the one it has always been — **transcript in, estimate plus
``status`` out**. The only new surface is the human-in-the-loop: a run can come back
``"awaiting_human_review"`` instead of finished, and there is a resume verb to answer
it. Everything else in the response (the routing history, the audit trail) is
observability the client may ignore.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.graph.state import BudgetMatch, Component


class SupervisorEstimateRequest(BaseModel):
    """START a supervisor run."""

    transcript: str = Field(min_length=100, max_length=50_000)
    estimation_id: str | None = Field(default=None, max_length=128)


class SupervisorResumeRequest(BaseModel):
    """The human's answer to the review gate.

    Typed, unlike Session 13's free-form ``decision: dict``: this flow has exactly one
    gate with exactly three verbs, so the contract can afford to be precise — and a
    typo in the decision becomes a 422 instead of a silently-approved estimate.
    """

    decision: Literal["approve", "adjust", "reject"]
    estimate_overrides: dict | None = Field(
        default=None,
        description="Fields the reviewer edited; merged over the estimate. Only "
        "meaningful for 'adjust'. Editing components rederives the headline total.",
    )
    note: str | None = Field(default=None, max_length=2000)


class PendingHumanReview(BaseModel):
    """What a paused run is waiting for — everything the reviewer needs to decide."""

    gate: str = "low_confidence_review"
    estimation_id: str
    reasons: list[str] = Field(
        default_factory=list, description="Which trigger conditions fired, in words."
    )
    confidence: float | None = None
    threshold: float | None = None
    estimate: dict | None = None
    validation: dict | None = None


class SupervisorRunState(BaseModel):
    """The state of a run: paused for a human, or completed."""

    estimation_id: str
    state: Literal["paused", "completed"]
    status: str = Field(
        description="validated | needs_review | rejected | awaiting_human_review. "
        "The last is derived while the run is paused; it is never stored in the graph "
        "state, because the run is genuinely mid-node at that point."
    )
    pending_review: PendingHumanReview | None = None

    estimate: dict | None = None
    confidence: float | None = None
    requirements: list[str] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    budget_matches: list[BudgetMatch] = Field(default_factory=list)
    validation: dict | None = None
    human_decision: dict | None = None

    # Observability the client may ignore — but which is what makes the supervisor's
    # decisions and the agents' privilege inspectable without opening a log viewer.
    routing_history: list[dict] = Field(default_factory=list)
    agent_contributions: list[dict] = Field(default_factory=list)
    privilege_violations: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
