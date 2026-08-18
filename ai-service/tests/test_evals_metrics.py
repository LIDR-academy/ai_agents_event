"""Unit tests for the deterministic eval metrics."""

from __future__ import annotations

from app.domain.schemas.estimation import EstimationResult
from evals.dataset import GoldenCase
from evals.metrics import (
    ContentRecallMetric,
    CostBoundsMetric,
    SchemaAdherenceMetric,
    run_all_metrics,
)


def _case(**kwargs) -> GoldenCase:
    defaults = {
        "id": "case-test",
        "transcript": "A" * 25 + " plain transcript",
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "phases_table",
    }
    return GoldenCase(**{**defaults, **kwargs})


def _good_result(
    *, total: int = 25_000, summary: str = "CRM build for sales team."
) -> EstimationResult:
    return EstimationResult(
        summary=summary,
        confidence_pct=70,
        phases=[
            {"name": "Discovery", "duration_weeks": 1, "cost_eur": 5_000,
             "summary": "Workshops and tech spike."},
            {"name": "Build", "duration_weeks": 4, "cost_eur": total - 5_000,
             "summary": "Core features in React + Postgres."},
        ],
        total_duration_weeks=5,
        total_cost_eur=total,
    )


def _oos_result() -> EstimationResult:
    return EstimationResult(
        summary="Out of scope: too vague to size.",
        confidence_pct=10,
        phases=[
            {"name": "Not estimated", "duration_weeks": 1, "cost_eur": 0,
             "summary": "Cannot be sized without more information."}
        ],
        total_duration_weeks=1,
        total_cost_eur=0,
    )


# ---- SchemaAdherenceMetric -----------------------------------------------


def test_schema_adherence_passes_on_balanced_result() -> None:
    metric = SchemaAdherenceMetric()
    result = metric.evaluate(_case(), _good_result())
    assert result.passed
    assert result.score == 1.0


def test_schema_adherence_flags_phase_count_outside_range() -> None:
    metric = SchemaAdherenceMetric()
    case = _case(expected_phase_count_range=(4, 6))
    result = metric.evaluate(case, _good_result())  # only 2 phases
    assert not result.passed
    assert "phase count" in result.details


# ---- CostBoundsMetric ----------------------------------------------------


def test_cost_bounds_passes_in_range() -> None:
    metric = CostBoundsMetric()
    case = _case(expected_cost_range_eur=(15_000, 60_000),
                 expected_duration_weeks_range=(2, 12))
    assert metric.evaluate(case, _good_result()).passed


def test_cost_bounds_flags_too_expensive() -> None:
    metric = CostBoundsMetric()
    case = _case(expected_cost_range_eur=(5_000, 10_000))
    result = metric.evaluate(case, _good_result(total=25_000))
    assert not result.passed
    assert "outside" in result.details


def test_cost_bounds_enforces_oos_envelope() -> None:
    metric = CostBoundsMetric()
    case = _case(expected_out_of_scope=True)
    # Real OoS envelope passes:
    assert metric.evaluate(case, _oos_result()).passed
    # Non-zero result flagged when OoS was expected:
    assert not metric.evaluate(case, _good_result()).passed


# ---- ContentRecallMetric --------------------------------------------------


def test_content_recall_passes_when_terms_appear() -> None:
    metric = ContentRecallMetric()
    case = _case(
        expected_in_summary=["CRM"],
        expected_technologies_any_of=["React"],
    )
    assert metric.evaluate(case, _good_result()).passed


def test_content_recall_flags_missing_term() -> None:
    metric = ContentRecallMetric()
    case = _case(expected_in_summary=["mobile app"])
    result = metric.evaluate(case, _good_result())
    assert not result.passed


def test_content_recall_skips_for_out_of_scope_case() -> None:
    metric = ContentRecallMetric()
    case = _case(expected_out_of_scope=True, expected_in_summary=["anything"])
    assert metric.evaluate(case, _oos_result()).passed


# ---- run_all_metrics aggregator ------------------------------------------


def test_run_all_metrics_returns_three_results() -> None:
    case = _case(
        expected_in_summary=["CRM"],
        expected_technologies_any_of=["React"],
        expected_cost_range_eur=(10_000, 50_000),
    )
    results = run_all_metrics(case, _good_result())
    assert [r.name for r in results] == ["schema_adherence", "cost_bounds", "content_recall"]
    assert all(r.passed for r in results)
