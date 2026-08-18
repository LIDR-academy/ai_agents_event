"""Three new metrics for the Session 6 stress exercise.

These live apart from ``evals.metrics`` because they operate over a
``TurnObservation`` (telemetry attached to the conversational response) and
a session snapshot dict (the JSON shape of ``GET /sessions/{id}``), not
over ``(GoldenCase, EstimationResult)``. The signature mismatch is the only
reason for the separate module — the ``MetricResult`` dataclass is reused
verbatim from ``evals.metrics`` to keep the report shape uniform.

Design choice: deterministic checks only. No embeddings, no LLM-as-judge.
``MemoryDriftMetric`` does a case-insensitive substring match against the
text of the session snapshot. That is enough to answer the question the
exercise actually asks ("at turn N, does the project_name from turn 1 still
survive somewhere?") and it has no failure modes of its own — when it says
the fact is gone, the fact is gone.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from app.domain.schemas.estimation import TurnObservation
from evals.metrics import MetricResult


FactField = Literal["project_name", "technologies", "scope", "summary", "any"]


class LatencyBudgetMetric:
    """Per-turn latency under a wall-clock budget.

    The runner instantiates this once with a budget (e.g. 8000 ms reflecting
    a customer-facing SLA) and calls ``evaluate`` for every turn.
    """

    name = "latency_budget"

    def __init__(self, budget_ms: int) -> None:
        if budget_ms <= 0:
            raise ValueError("budget_ms must be positive")
        self.budget_ms = budget_ms

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        passed = observation.latency_ms <= self.budget_ms
        return MetricResult(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details=f"{observation.latency_ms} ms vs budget {self.budget_ms} ms",
        )


class CostBudgetMetric:
    """Per-turn cost under a USD budget.

    Reads ``cost_usd`` from the observation (already computed by the LLM
    wrapper from ``MODEL_COSTS`` and the actual token counts of this turn).
    The budget tests the cost of a *single* turn, not the cumulative spend
    across the session — that aggregate is computed by the runner from the
    per-turn observations.
    """

    name = "cost_budget"

    def __init__(self, budget_usd: float) -> None:
        if budget_usd <= 0:
            raise ValueError("budget_usd must be positive")
        self.budget_usd = budget_usd

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        passed = observation.cost_usd <= self.budget_usd
        return MetricResult(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details=f"${observation.cost_usd:.6f} vs budget ${self.budget_usd:.6f}",
        )


class MemoryDriftMetric:
    """Does a fact introduced in an earlier turn still appear in the session?

    Takes a ``fact`` string (e.g. ``"Nimbus"``, ``"Flutter"``, ``"80000"``)
    and an optional ``fact_field`` that narrows where to look:

    - ``"project_name"`` — only ``metadata.project_name``.
    - ``"technologies"`` — only ``metadata.mentioned_technologies``.
    - ``"scope"``        — only ``metadata.agreed_scope``.
    - ``"summary"``      — only the conversation summary (after compression).
    - ``"any"``          — anywhere in the snapshot's JSON serialisation
                            (includes anchors, metadata, summary, tier rule).

    Matching is case-insensitive substring. That is enough for the exercise
    and avoids the "is 'Flutter' the same as 'Flutter SDK'?" rabbit hole.
    """

    name = "memory_drift"

    def __init__(self, fact: str, fact_field: FactField = "any") -> None:
        if not fact:
            raise ValueError("fact must be a non-empty string")
        self.fact = fact
        self.needle = fact.lower()
        self.fact_field = fact_field

    def evaluate(self, snapshot: dict[str, Any]) -> MetricResult:
        haystack = self._haystack(snapshot)
        found = self.needle in haystack.lower()
        return MetricResult(
            name=self.name,
            score=1.0 if found else 0.0,
            passed=found,
            details=(
                f"fact={self.fact!r} field={self.fact_field} "
                f"{'present' if found else 'missing'}"
            ),
        )

    def _haystack(self, snapshot: dict[str, Any]) -> str:
        """Pluck the slice of the snapshot the field selector cares about."""
        metadata = snapshot.get("metadata") or {}
        if self.fact_field == "project_name":
            return str(metadata.get("project_name") or "")
        if self.fact_field == "technologies":
            return " ".join(metadata.get("mentioned_technologies") or [])
        if self.fact_field == "scope":
            return str(metadata.get("agreed_scope") or "")
        if self.fact_field == "summary":
            return str(snapshot.get("summary") or "")
        # "any": serialise the whole snapshot once; substring search is O(n).
        return json.dumps(snapshot, default=str)
