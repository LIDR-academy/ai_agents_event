"""Validation tests for the EstimationRequest and EstimationResult contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.schemas.estimation import (
    DetailLevel,
    EstimationRequest,
    EstimationResult,
    OutputFormat,
    Phase,
    ProjectType,
)


VALID_PAYLOAD = {
    "description": "A small B2B SaaS to manage employee equipment loans across teams.",
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table",
}


# --- EstimationRequest ------------------------------------------------------


def test_valid_request_constructs_with_typed_enums() -> None:
    request = EstimationRequest(**VALID_PAYLOAD)
    assert request.description == VALID_PAYLOAD["description"]
    assert request.project_type is ProjectType.WEB_SAAS
    assert request.detail_level is DetailLevel.MEDIUM
    assert request.output_format is OutputFormat.PHASES_TABLE


def test_description_below_minimum_length_fails() -> None:
    payload = {**VALID_PAYLOAD, "description": "too short"}
    with pytest.raises(ValidationError) as exc_info:
        EstimationRequest(**payload)
    assert any(err["loc"] == ("description",) for err in exc_info.value.errors())


def test_description_above_maximum_length_fails() -> None:
    payload = {**VALID_PAYLOAD, "description": "x" * 80001}
    with pytest.raises(ValidationError) as exc_info:
        EstimationRequest(**payload)
    assert any(err["loc"] == ("description",) for err in exc_info.value.errors())


@pytest.mark.parametrize(
    "field",
    ["project_type", "detail_level", "output_format"],
)
def test_each_enum_rejects_unknown_values(field: str) -> None:
    payload = {**VALID_PAYLOAD, field: "definitely_not_a_real_value"}
    with pytest.raises(ValidationError) as exc_info:
        EstimationRequest(**payload)
    assert any(err["loc"] == (field,) for err in exc_info.value.errors())


def test_missing_required_enum_fails() -> None:
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "project_type"}
    with pytest.raises(ValidationError) as exc_info:
        EstimationRequest(**payload)
    assert any(err["loc"] == ("project_type",) for err in exc_info.value.errors())


# --- EstimationResult model_validators --------------------------------------


def _valid_result(**overrides: object) -> dict[str, object]:
    base = {
        "summary": "Solid mid-size SaaS with login and admin dashboard.",
        "total_duration_weeks": 8,
        "total_cost_eur": 30_000,
        "confidence_pct": 70,
        "phases": [
            {"name": "Discovery", "duration_weeks": 1, "cost_eur": 5_000,
             "summary": "Workshops, scoping and tech spike."},
            {"name": "Implementation", "duration_weeks": 6, "cost_eur": 20_000,
             "summary": "Build and integrate the core SaaS features."},
            {"name": "QA + launch", "duration_weeks": 1, "cost_eur": 5_000,
             "summary": "Test pass and production rollout."},
        ],
    }
    base.update(overrides)
    return base


def test_phases_sum_must_equal_total_cost() -> None:
    bad = _valid_result(total_cost_eur=31_000)  # phases still sum to 30_000
    with pytest.raises(ValidationError) as exc_info:
        EstimationResult(**bad)
    msg = str(exc_info.value)
    assert "phases sum" in msg and "total_cost_eur" in msg


def test_low_confidence_requires_out_of_scope_prefix() -> None:
    bad = _valid_result(confidence_pct=10)  # summary does not start with "Out of scope:"
    with pytest.raises(ValidationError) as exc_info:
        EstimationResult(**bad)
    assert "Out of scope:" in str(exc_info.value)


def test_low_confidence_with_correct_prefix_passes() -> None:
    ok = _valid_result(
        confidence_pct=15,
        summary="Out of scope: the description does not say anything about scale or auth.",
    )
    EstimationResult(**ok)


def test_high_confidence_accepts_any_summary_prefix() -> None:
    ok = _valid_result(confidence_pct=85, summary="Looks like a standard B2B SaaS build.")
    EstimationResult(**ok)


def test_phase_bounds_are_enforced() -> None:
    bad_phase = _valid_result(
        phases=[
            {"name": "x", "duration_weeks": 0, "cost_eur": 0, "summary": "too short"}
        ],
        total_cost_eur=0,
        total_duration_weeks=1,
    )
    with pytest.raises(ValidationError):
        EstimationResult(**bad_phase)


def test_phase_directly_validates() -> None:
    with pytest.raises(ValidationError):
        Phase(name="", duration_weeks=1, cost_eur=0, summary="enough text")
