"""DTOs for the Actor-Critic-Boss audit trail.

Lives in ``schemas`` (not ``services``) so it can be referenced from both
the orchestrator (``app/services/boss.py``) and the response model
(``app/schemas/estimation.py::ACBResponse``) without import cycles.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


BossDecision = Literal["accept", "iterate", "synthesize"]


class ACBIteration(BaseModel):
    """Audit record for a single actor+critic round.

    Persisted in the response so the caller can show the full trace in the UI
    (or in a debug panel) and so we can reason about the system end-to-end.
    """

    iteration: int = Field(ge=0)
    decision_after: BossDecision
    critic_verdict: str
    critic_confidence: int = Field(ge=0, le=100)
    issue_summary: list[str] = Field(default_factory=list)


class BossTrace(BaseModel):
    iterations: list[ACBIteration] = Field(default_factory=list)
    final_decision: BossDecision
    iterations_run: int = Field(ge=0)
