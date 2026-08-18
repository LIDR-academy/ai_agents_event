"""Unit tests for the Critic — schema validators and service wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.domain.schemas.critic import CriticFeedback, CriticIssue
from app.domain.schemas.estimation import EstimationResult
from app.generation.agentic.critic import Critic
from app.generation.conversation.models import ProjectMetadata
from app.generation.conversation.tier_resolver import Tier


def _canned_result() -> EstimationResult:
    return EstimationResult(
        summary="Mid-size CRM build for the sales team.",
        confidence_pct=70,
        phases=[
            {"name": "Discovery", "duration_weeks": 1, "cost_eur": 5_000, "summary": "Workshops + tech spike."},
            {"name": "Build", "duration_weeks": 5, "cost_eur": 20_000, "summary": "Core CRM features."},
        ],
        total_duration_weeks=6,
        total_cost_eur=25_000,
    )


# --- Schema validators -----------------------------------------------------


def test_accept_with_empty_issues_is_valid() -> None:
    feedback = CriticFeedback(verdict="accept", issues=[], confidence_in_review=80)
    assert feedback.verdict == "accept"


def test_needs_iteration_requires_blocking_issue() -> None:
    with pytest.raises(ValidationError):
        CriticFeedback(
            verdict="needs_iteration",
            issues=[
                CriticIssue(
                    category="tier_mismatch",
                    severity="minor",
                    field_path="summary",
                    description="too jargony",
                )
            ],
            confidence_in_review=70,
        )


def test_needs_iteration_with_major_issue_is_valid() -> None:
    feedback = CriticFeedback(
        verdict="needs_iteration",
        issues=[
            CriticIssue(
                category="math_error",
                severity="critical",
                field_path="total_cost_eur",
                description="sum mismatch",
                suggested_fix="recompute totals",
            )
        ],
        confidence_in_review=60,
    )
    assert feedback.verdict == "needs_iteration"


def test_reject_requires_at_least_one_issue() -> None:
    with pytest.raises(ValidationError):
        CriticFeedback(verdict="reject", issues=[], confidence_in_review=20)


# --- Service wiring --------------------------------------------------------


def test_critic_review_returns_canned_feedback() -> None:
    expected = CriticFeedback(verdict="accept", issues=[], confidence_in_review=90)
    wrapper = MagicMock()
    wrapper.complete_structured_chat.return_value = (
        expected,
        {"model": "gpt-4o-mini", "provider": "openai", "latency_ms": 1},
    )

    critic = Critic(llm_wrapper=wrapper, model="gpt-4o-mini")
    feedback = critic.review(
        transcript="...",
        metadata=ProjectMetadata(),
        tier=Tier.DEFAULT,
        result=_canned_result(),
    )
    assert feedback is expected

    # The Critic must call the chat method with response_model=CriticFeedback.
    kwargs = wrapper.complete_structured_chat.call_args.kwargs
    assert kwargs["response_model"] is CriticFeedback
    assert kwargs["model_override"] == "gpt-4o-mini"
    # System prompt should be first message.
    assert kwargs["messages"][0]["role"] == "system"


def test_critic_failure_falls_back_to_accept() -> None:
    wrapper = MagicMock()
    wrapper.complete_structured_chat.side_effect = RuntimeError("upstream down")

    critic = Critic(llm_wrapper=wrapper, model="gpt-4o-mini")
    feedback = critic.review(
        transcript="...",
        metadata=ProjectMetadata(),
        tier=Tier.DEFAULT,
        result=_canned_result(),
    )
    assert feedback.verdict == "accept"
    assert feedback.confidence_in_review == 0
    assert feedback.issues == []
