"""The human-review gate: the trigger signal and how a decision folds into state.

Each of the three conditions is pinned in isolation, because the value of a
conditional gate is entirely in WHEN it fires. A gate that always fires is a form.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.domain.graph.supervisor.gate import (
    _apply_decision,
    human_review_gate,
    needs_human_review,
    review_reasons,
)


def _settings(**overrides) -> Settings:
    base = {
        "OPENAI_API_KEY": "sk-test",
        "SUPERVISOR_CONFIDENCE_THRESHOLD": 0.6,
        "SUPERVISOR_MIN_GROUNDED_RATIO": 0.5,
    }
    base.update(overrides)
    return Settings(**base)


def _healthy_state() -> dict:
    return {
        "confidence": 0.9,
        "out_of_range": False,
        "grounded_components": 2,
        "components": [{"name": "A", "category": "backend"}, {"name": "B", "category": "mobile"}],
        "estimate": {"total_engineer_days": 20},
    }


def test_a_healthy_estimate_does_not_pause():
    assert review_reasons(_healthy_state(), _settings()) == []
    assert needs_human_review(_healthy_state(), _settings()) is False


def test_trigger_1_low_confidence():
    state = {**_healthy_state(), "confidence": 0.31}
    reasons = review_reasons(state, _settings())
    assert len(reasons) == 1
    assert "0.31" in reasons[0] and "below" in reasons[0]


def test_trigger_2_out_of_historical_range():
    state = {**_healthy_state(), "out_of_range": True}
    reasons = review_reasons(state, _settings())
    assert len(reasons) == 1
    assert "plausible range" in reasons[0]


def test_trigger_3_no_precedent_in_budgets():
    state = {**_healthy_state(), "grounded_components": 0}
    reasons = review_reasons(state, _settings())
    assert len(reasons) == 1
    assert "0/2 components" in reasons[0]


def test_all_three_conditions_report_separately():
    state = {
        **_healthy_state(),
        "confidence": 0.1,
        "out_of_range": True,
        "grounded_components": 0,
    }
    assert len(review_reasons(state, _settings())) == 3


def test_the_threshold_is_configurable():
    """The knob of the exercise: raising it sends more estimates to a person."""
    state = {**_healthy_state(), "confidence": 0.8}
    assert review_reasons(state, _settings()) == []
    assert review_reasons(state, _settings(SUPERVISOR_CONFIDENCE_THRESHOLD=0.9))


def test_confidence_of_none_does_not_trigger():
    """A run that never reached the validator must not be treated as low confidence."""
    state = {**_healthy_state(), "confidence": None}
    assert review_reasons(state, _settings()) == []


# --------------------------------------------------------------------------- #
# Folding the human's decision                                                 #
# --------------------------------------------------------------------------- #
def test_approve_validates_the_estimate_untouched():
    state = {"estimate": {"total_engineer_days": 20}}
    estimate, status = _apply_decision(state, {"decision": "approve"})
    assert status == "validated"
    assert estimate["total_engineer_days"] == 20


def test_reject_keeps_the_estimate_as_evidence():
    state = {"estimate": {"total_engineer_days": 20}}
    estimate, status = _apply_decision(state, {"decision": "reject", "note": "out of scope"})
    assert status == "rejected"
    assert estimate["total_engineer_days"] == 20


def test_adjust_merges_overrides_and_rederives_the_total():
    """A shallow merge would leave the headline total contradicting the components."""
    state = {
        "estimate": {
            "components": [{"name": "A", "engineer_days": 10}],
            "total_engineer_days": 10,
        }
    }
    estimate, status = _apply_decision(
        state,
        {
            "decision": "adjust",
            "estimate_overrides": {
                "components": [
                    {"name": "A", "engineer_days": 12},
                    {"name": "B", "engineer_days": 8},
                ]
            },
        },
    )
    assert status == "validated"
    assert estimate["total_engineer_days"] == 20


@pytest.mark.asyncio
async def test_gate_falls_through_without_interrupting_when_healthy():
    """No trigger means no pause — ``interrupt()`` is never reached."""
    update = await human_review_gate(_healthy_state())
    assert update == {"needs_human_review": False, "review_reasons": []}
