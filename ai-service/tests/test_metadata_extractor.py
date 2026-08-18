"""Unit tests for the metadata extractor (Session 5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.domain.schemas.estimation import EstimationResult
from app.generation.conversation.metadata_extractor import update_metadata
from app.generation.conversation.models import ProjectMetadata


def _canned_result() -> EstimationResult:
    return EstimationResult(
        summary="MVP CRM build for the sales team.",
        confidence_pct=70,
        phases=[
            {"name": "Discovery", "duration_weeks": 1, "cost_eur": 5_000, "summary": "Workshops + tech spike."},
            {"name": "Build", "duration_weeks": 5, "cost_eur": 20_000, "summary": "Core CRM features."},
        ],
        total_duration_weeks=6,
        total_cost_eur=25_000,
    )


def test_update_metadata_merges_extracted_with_previous() -> None:
    previous = ProjectMetadata(
        project_name="Nimbus",
        mentioned_technologies=["React"],
    )
    extracted = ProjectMetadata(
        project_name=None,
        assumed_team_size=3,
        mentioned_technologies=["Postgres"],
        agreed_scope="Phase 1 MVP",
    )
    wrapper = MagicMock()
    wrapper.complete_structured_chat.return_value = (
        extracted,
        {"model": "gpt-4o-mini", "provider": "openai", "latency_ms": 42},
    )

    merged = update_metadata(
        previous=previous,
        transcript="Add Postgres for storage.",
        result=_canned_result(),
        llm_wrapper=wrapper,
        model="gpt-4o-mini",
    )

    assert merged.project_name == "Nimbus"  # preserved
    assert merged.assumed_team_size == 3  # added
    assert merged.agreed_scope == "Phase 1 MVP"  # added
    assert sorted(merged.mentioned_technologies) == ["Postgres", "React"]


def test_update_metadata_returns_previous_on_failure() -> None:
    previous = ProjectMetadata(project_name="Nimbus")

    wrapper = MagicMock()
    wrapper.complete_structured_chat.side_effect = RuntimeError("upstream down")

    result = update_metadata(
        previous=previous,
        transcript="hello",
        result=_canned_result(),
        llm_wrapper=wrapper,
        model="gpt-4o-mini",
    )
    assert result is previous


def test_update_metadata_forwards_overrides(monkeypatch: Any) -> None:
    wrapper = MagicMock()
    wrapper.complete_structured_chat.return_value = (
        ProjectMetadata(),
        {"model": "gpt-4o-mini", "provider": "openai", "latency_ms": 0},
    )
    update_metadata(
        previous=ProjectMetadata(),
        transcript="x",
        result=_canned_result(),
        llm_wrapper=wrapper,
        model="gpt-4o-mini",
    )
    kwargs = wrapper.complete_structured_chat.call_args.kwargs
    assert kwargs["model_override"] == "gpt-4o-mini"
    assert kwargs["response_model"] is ProjectMetadata
    assert len(kwargs["messages"]) == 2
    assert kwargs["messages"][0]["role"] == "system"
