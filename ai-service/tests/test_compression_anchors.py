"""Unit tests for the heuristic anchor detector."""

from __future__ import annotations

import pytest

from app.generation.conversation.compression.anchors import AnchorDetector
from app.generation.conversation.models import Message


@pytest.fixture
def detector() -> AnchorDetector:
    return AnchorDetector(mode="heuristic")


@pytest.mark.parametrize(
    "text, expected_rule",
    [
        ("We signed the NDA last week and the SOW yesterday.", "nda"),
        ("Heads-up: scope is frozen until Q3.", "scope_frozen"),
        ("Budget is locked at 80k EUR for phase one.", "budget_locked"),
        ("This is a HIPAA-regulated system, audit trail mandatory.", "compliance"),
        ("There's a hard deadline of November 15th.", "deadline_hard"),
        ("We are contractually bound to deliver before Christmas.", "contractual"),
        ("We agreed to a 3-month MVP with the customer.", "explicit_commitment"),
        ("We countersigned the MSA after a small redline.", "signed_contract"),
    ],
)
def test_heuristic_positive_cases(detector: AnchorDetector, text: str, expected_rule: str) -> None:
    match = detector.detect(Message(role="user", content=text))
    assert match.is_anchor, f"expected {text!r} to anchor"
    assert expected_rule in match.matched_rules


@pytest.mark.parametrize(
    "text",
    [
        "We need an SDK to integrate with their CRM.",
        "Add OAuth login next sprint.",
        "Looking at React + Postgres for the stack.",
        "Could you re-run the estimation with a smaller team?",
    ],
)
def test_heuristic_negative_cases(detector: AnchorDetector, text: str) -> None:
    match = detector.detect(Message(role="user", content=text))
    assert not match.is_anchor, f"expected {text!r} NOT to anchor"
    assert match.matched_rules == []


def test_llm_mode_falls_back_to_heuristic_when_wrapper_missing() -> None:
    detector = AnchorDetector(mode="llm", llm_wrapper=None)
    match = detector.detect(Message(role="user", content="We signed the NDA."))
    assert match.is_anchor
    assert "nda" in match.matched_rules


def test_llm_mode_calls_wrapper_when_provided() -> None:
    from unittest.mock import MagicMock
    from app.generation.conversation.compression.anchors import _AnchorClassification

    wrapper = MagicMock()
    wrapper.complete_structured_chat.return_value = (
        _AnchorClassification(is_anchor=True, reason="commits to compliance"),
        {"model": "gpt-4o-mini", "provider": "openai", "latency_ms": 1},
    )
    detector = AnchorDetector(mode="llm", llm_wrapper=wrapper)
    match = detector.detect(Message(role="user", content="Vague non-keyword phrasing here."))
    assert match.is_anchor
    assert match.matched_rules[0].startswith("llm:")
