"""Tests for ``enforce_scope_response`` (output filter policy)."""

from __future__ import annotations

from app.foundation.guardrails.output import enforce_scope_response
from app.domain.schemas.estimation import EstimationResult, OUT_OF_SCOPE_PREFIX


def _build(*, confidence_pct: int, summary: str) -> EstimationResult:
    """Build a valid EstimationResult avoiding the schema validators that would
    block us from constructing a low-confidence-without-prefix instance.

    We must satisfy both: phases sum == total_cost_eur, AND low-confidence has
    the "Out of scope:" prefix. To test the filter we use ``model_construct``
    which bypasses validators.
    """
    return EstimationResult.model_construct(
        summary=summary,
        confidence_pct=confidence_pct,
        phases=[
            {
                "name": "Discovery",
                "duration_weeks": 1,
                "cost_eur": 2_500,
                "summary": "Workshop and scoping.",
            }
        ],
        total_duration_weeks=1,
        total_cost_eur=2_500,
    )


def test_high_confidence_passes_through_untouched() -> None:
    original = _build(confidence_pct=80, summary="Solid mid-size SaaS build.")
    out = enforce_scope_response(original)
    assert out is original  # exact same instance, no rewrite


def test_low_confidence_with_correct_prefix_passes_through() -> None:
    original = _build(
        confidence_pct=15,
        summary=f"{OUT_OF_SCOPE_PREFIX} the description is too vague.",
    )
    out = enforce_scope_response(original)
    assert out is original


def test_low_confidence_without_prefix_gets_rewritten() -> None:
    original = _build(
        confidence_pct=10,
        summary="A standard SaaS project around 30k.",
    )
    out = enforce_scope_response(original)
    assert out is not original
    assert out.summary.startswith(OUT_OF_SCOPE_PREFIX)
    assert out.confidence_pct == 10  # preserved
    # Numeric fields normalised so the response is still well-formed.
    assert out.total_cost_eur == 0
    assert out.total_duration_weeks == 1
    assert len(out.phases) == 1
    assert out.phases[0].name == "Not estimated"
    # Original rationale is preserved as context.
    assert "A standard SaaS project around 30k." in out.summary


def test_filter_never_raises_even_with_pathological_input() -> None:
    """No matter the field combination, the filter returns a valid model."""
    original = _build(confidence_pct=0, summary="x" * 1000)
    out = enforce_scope_response(original)
    # Schema would have refused validation; we accept it via model_construct
    # but ensure the rewrite produces a valid EstimationResult.
    EstimationResult.model_validate(out.model_dump())
