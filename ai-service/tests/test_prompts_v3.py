"""Tests for the v3 conversational prompt template with tier-driven audience."""

from __future__ import annotations

import pytest

from app.foundation.prompts.loader import render_conversational_prompt
from app.domain.schemas.estimation import DetailLevel, OutputFormat, ProjectType
from app.generation.conversation.models import ProjectMetadata
from app.generation.conversation.tier_resolver import Tier


def _render(tier: Tier | None = None, **kwargs):
    return render_conversational_prompt(
        description="A " + "B" * 30,
        project_type=ProjectType.WEB_SAAS,
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
        metadata=ProjectMetadata(),
        version="v3",
        tier=tier,
        **kwargs,
    )


def test_v3_default_renders_default_audience_block() -> None:
    system, _user = _render(tier=Tier.DEFAULT)
    assert "<audience>" in system
    assert "Default behaviour" in system or "default behaviour" in system.lower()
    # No tier-specific phrases bleed through.
    assert "C-level" not in system
    assert "tech lead" not in system.lower()


@pytest.mark.parametrize(
    "tier, marker",
    [
        (Tier.EXECUTIVE, "C-level"),
        (Tier.PM, "project manager"),
        (Tier.DEVELOPER, "tech lead"),
    ],
)
def test_v3_tier_includes_marker_and_excludes_other_tiers(tier: Tier, marker: str) -> None:
    system, _user = _render(tier=tier)
    assert marker.lower() in system.lower()
    # Other tier markers should NOT appear in the same render.
    other_markers = {"C-level", "project manager", "tech lead"} - {marker}
    for other in other_markers:
        assert other.lower() not in system.lower(), f"unexpected leak: {other} when tier={tier}"


def test_v3_user_includes_critic_feedback_block_when_provided() -> None:
    from types import SimpleNamespace

    feedback = SimpleNamespace(
        issues=[
            SimpleNamespace(
                severity="critical",
                category="math_error",
                field_path="phases[1].cost_eur",
                description="cost mismatch",
                suggested_fix="recompute the sum",
            )
        ]
    )
    _system, user = _render(tier=Tier.DEFAULT, critic_feedback=feedback)
    assert "<critic_feedback>" in user
    assert "[critical]" in user
    assert "phases[1].cost_eur" in user
    assert "recompute the sum" in user


def test_v3_user_omits_critic_feedback_block_when_none() -> None:
    _system, user = _render(tier=Tier.DEFAULT)
    assert "<critic_feedback>" not in user


def test_v3_falls_back_to_default_audience_when_tier_is_none() -> None:
    system, _user = _render(tier=None)
    assert "Default behaviour" in system or "default behaviour" in system.lower()
