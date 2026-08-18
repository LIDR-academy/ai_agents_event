"""Unit tests for the Session 12 agent tools (no network, no DB)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.generation.agentic.agent_tools import (
    derive_task_hours,
    dispatch_tool,
    search_budgets,
    validate_estimate,
)
from app.generation.rag.task_hours import distance_weighted_consensus


# --- derive_task_hours ----------------------------------------------------- #
def test_derive_task_hours_uses_distance_weighted_consensus():
    # Two analogs: 100h very close (0.05), 300h far (0.8). The close one dominates,
    # so the consensus lands well below the arithmetic mean (200).
    result = derive_task_hours(
        {
            "module": "Auth",
            "task": "OAuth backend",
            "neighbors": [
                {"estimated_hours": 100, "distance": 0.05, "source_id": 1, "budget_id": "B-1"},
                {"estimated_hours": 300, "distance": 0.8, "source_id": 2, "budget_id": "B-2"},
            ],
        },
        consensus_fn=distance_weighted_consensus,
    )
    assert result["has_match"] is True
    assert 100 <= result["estimated_hours"] < 200  # weighted toward the closer analog
    assert 0.0 <= result["reliability"] <= 1.0


def test_derive_task_hours_matches_the_deterministic_consensus_exactly():
    neighbors = [
        {"estimated_hours": 120, "distance": 0.1, "source_id": 1, "budget_id": None},
        {"estimated_hours": 180, "distance": 0.2, "source_id": 2, "budget_id": None},
    ]
    result = derive_task_hours(
        {"module": "M", "task": "T", "neighbors": neighbors}, consensus_fn=distance_weighted_consensus
    )
    hours, reliability, _ = distance_weighted_consensus([(120, 0.1), (180, 0.2)])
    assert result["estimated_hours"] == hours
    assert result["reliability"] == reliability


def test_derive_task_hours_no_neighbors_is_a_no_match_not_a_zero():
    result = derive_task_hours(
        {"module": "M", "task": "Mystery", "neighbors": []},
        consensus_fn=distance_weighted_consensus,
    )
    assert result["has_match"] is False
    assert "estimated_hours" not in result  # never fabricates a number


def test_derive_task_hours_rejects_bad_args():
    with pytest.raises(ValidationError):
        # neighbour missing the required distance
        derive_task_hours(
            {"module": "M", "task": "T", "neighbors": [{"estimated_hours": 100}]},
            consensus_fn=distance_weighted_consensus,
        )


# --- validate_estimate ----------------------------------------------------- #
def test_validate_estimate_passes_clean_estimate():
    result = validate_estimate(
        {
            "components": [{"name": "A", "estimated_hours": 115.0, "reference_amounts": [100.0]}],
            "total_hours": 115.0,
        }
    )
    assert result["ok"] is True
    assert result["issues"] == []


def test_validate_estimate_flags_unbudgeted_and_total_mismatch():
    result = validate_estimate(
        {
            "components": [{"name": "A", "estimated_hours": 50.0, "reference_amounts": []}],
            "total_hours": 999.0,
        }
    )
    assert result["ok"] is False
    joined = " ".join(result["issues"]).lower()
    assert "no historical reference" in joined
    assert "does not match" in joined


def test_validate_estimate_flags_out_of_range_component():
    # reference 100 → plausible range [50, 200]; 1000 is out of range.
    result = validate_estimate(
        {
            "components": [{"name": "A", "estimated_hours": 1000.0, "reference_amounts": [100.0]}],
            "total_hours": 1000.0,
        }
    )
    assert result["ok"] is False
    assert any("outside the plausible range" in issue for issue in result["issues"])


def test_validate_estimate_flags_nonpositive_total():
    result = validate_estimate({"components": [], "total_hours": 0.0})
    assert result["ok"] is False
    assert any("non-positive" in issue for issue in result["issues"])


# --- search_budgets + dispatch --------------------------------------------- #
async def test_search_budgets_uses_injected_backend():
    async def fake_backend(query: str, sectors: list[str] | None) -> list[dict]:
        assert query == "auth backend"
        assert sectors is None
        return [{"id": 1, "estimated_hours": 420.0, "content_preview": "x", "distance": 0.1}]

    result = await search_budgets({"query": "auth backend", "filters": None}, backend=fake_backend)
    assert result["count"] == 1
    assert "420.0" in result["summary"]


async def test_search_budgets_passes_sector_filter_to_backend():
    seen: dict = {}

    async def fake_backend(query: str, sectors: list[str] | None) -> list[dict]:
        seen["sectors"] = sectors
        return []

    await search_budgets(
        {"query": "logistics tracking", "filters": {"sectors": ["logistics"], "component_type": None}},
        backend=fake_backend,
    )
    assert seen["sectors"] == ["logistics"]


async def test_search_budgets_rejects_empty_query():
    async def fake_backend(query: str, sectors: list[str] | None) -> list[dict]:
        return []

    with pytest.raises(ValidationError):
        await search_budgets({"query": "", "filters": None}, backend=fake_backend)


async def test_dispatch_routes_and_rejects_unknown_tool():
    async def fake_backend(query: str, sectors: list[str] | None) -> list[dict]:
        return [{"id": 1, "estimated_hours": 100.0, "distance": 0.1}]

    ok = await dispatch_tool(
        "search_budgets",
        {"query": "x", "filters": None},
        backend=fake_backend,
        consensus_fn=distance_weighted_consensus,
    )
    assert ok["count"] == 1

    with pytest.raises(ValueError, match="Unknown tool"):
        await dispatch_tool(
            "nonexistent", {}, backend=fake_backend, consensus_fn=distance_weighted_consensus
        )
