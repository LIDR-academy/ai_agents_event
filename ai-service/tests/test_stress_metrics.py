"""Unit tests for the Session 6 stress metrics.

Three behaviours per metric: a clear pass, a clear fail, and the boundary
case (latency == budget, cost == budget, fact found via "any" fallback).
All deterministic — no network, no LLM, no fixtures on disk.
"""

from __future__ import annotations

import pytest

from app.domain.schemas.estimation import TurnObservation
from evals.stress.metrics import (
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
)


def _obs(**overrides) -> TurnObservation:
    """Build a minimal valid TurnObservation, override what each test needs."""
    base = dict(
        turn_index=1,
        session_id="sess-test",
        enriched_transcript_chars=200,
        attachments_total_chars=0,
        messages_in_window=2,
        anchors_count=0,
        summary_chars=0,
        tokens_in=500,
        tokens_out=300,
        cost_usd=0.001,
        latency_ms=1500,
    )
    base.update(overrides)
    return TurnObservation(**base)


# -- LatencyBudgetMetric ----------------------------------------------------


def test_latency_under_budget_passes():
    metric = LatencyBudgetMetric(budget_ms=8000)
    result = metric.evaluate(_obs(latency_ms=3200))
    assert result.passed is True
    assert result.score == 1.0
    assert "3200" in result.details


def test_latency_over_budget_fails():
    metric = LatencyBudgetMetric(budget_ms=8000)
    result = metric.evaluate(_obs(latency_ms=9100))
    assert result.passed is False
    assert result.score == 0.0


def test_latency_exactly_at_budget_passes():
    """Boundary: latency == budget passes (the comparison is ``<=``)."""
    metric = LatencyBudgetMetric(budget_ms=4000)
    result = metric.evaluate(_obs(latency_ms=4000))
    assert result.passed is True


def test_latency_budget_must_be_positive():
    with pytest.raises(ValueError):
        LatencyBudgetMetric(budget_ms=0)


# -- CostBudgetMetric -------------------------------------------------------


def test_cost_under_budget_passes():
    metric = CostBudgetMetric(budget_usd=0.02)
    result = metric.evaluate(_obs(cost_usd=0.0035))
    assert result.passed is True


def test_cost_over_budget_fails():
    metric = CostBudgetMetric(budget_usd=0.002)
    result = metric.evaluate(_obs(cost_usd=0.01))
    assert result.passed is False
    assert result.score == 0.0


def test_cost_exactly_at_budget_passes():
    metric = CostBudgetMetric(budget_usd=0.005)
    result = metric.evaluate(_obs(cost_usd=0.005))
    assert result.passed is True


# -- MemoryDriftMetric ------------------------------------------------------


def _snapshot(
    *,
    project_name: str | None = None,
    technologies: list[str] | None = None,
    scope: str | None = None,
    summary: str | None = None,
    extra: dict | None = None,
) -> dict:
    snap = {
        "session_id": "sess-test",
        "message_count": 4,
        "metadata": {
            "project_name": project_name,
            "mentioned_technologies": technologies or [],
            "agreed_scope": scope,
            "assumed_team_size": None,
        },
        "summary": summary,
    }
    if extra:
        snap.update(extra)
    return snap


def test_drift_fact_present_in_project_name_passes():
    metric = MemoryDriftMetric(fact="Nimbus", fact_field="project_name")
    result = metric.evaluate(_snapshot(project_name="Nimbus"))
    assert result.passed is True
    assert "Nimbus" in result.details


def test_drift_fact_missing_from_project_name_fails():
    metric = MemoryDriftMetric(fact="Nimbus", fact_field="project_name")
    result = metric.evaluate(_snapshot(project_name="Atlas"))
    assert result.passed is False


def test_drift_fact_present_in_technologies_passes():
    metric = MemoryDriftMetric(fact="Flutter", fact_field="technologies")
    result = metric.evaluate(
        _snapshot(technologies=["React", "Flutter", "Postgres"])
    )
    assert result.passed is True


def test_drift_fact_present_in_summary_passes():
    metric = MemoryDriftMetric(fact="budget locked at 80k", fact_field="summary")
    result = metric.evaluate(
        _snapshot(summary="Turn 8: client confirms budget locked at 80k EUR.")
    )
    assert result.passed is True


def test_drift_fact_case_insensitive_match():
    metric = MemoryDriftMetric(fact="NIMBUS", fact_field="project_name")
    result = metric.evaluate(_snapshot(project_name="nimbus"))
    assert result.passed is True


def test_drift_any_field_searches_full_snapshot():
    """``fact_field='any'`` matches anywhere in the serialised snapshot —
    including anchor turn content and free-form fields."""
    metric = MemoryDriftMetric(fact="signed NDA", fact_field="any")
    snap = _snapshot(extra={"anchors": [{"role": "user", "content": "We signed NDA last week."}]})
    result = metric.evaluate(snap)
    assert result.passed is True


def test_drift_any_field_misses_when_truly_absent():
    metric = MemoryDriftMetric(fact="Cerner", fact_field="any")
    result = metric.evaluate(_snapshot(project_name="Nimbus"))
    assert result.passed is False


def test_drift_empty_fact_rejected():
    with pytest.raises(ValueError):
        MemoryDriftMetric(fact="")
