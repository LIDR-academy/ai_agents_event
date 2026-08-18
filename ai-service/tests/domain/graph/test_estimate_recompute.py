"""The estimate totals arithmetic (Session 13 gate-2 editable hours) — no network.

``recompute_estimate_totals`` is the single source of truth used by both
``build_estimate`` and ``human_gate_analysis`` (after applying the human's
``estimate_overrides``), so the headline days/ratio/confidence stay consistent with
whatever hours the human completes at gate 2.
"""

from __future__ import annotations

from app.domain.graph.agents._common import build_estimate, recompute_estimate_totals


def _modules(*pairs):
    # pairs: (task_name, estimated_hours) ; hours None = ungrounded.
    return [{"name": "M", "tasks": [{"name": n, "estimated_hours": h} for n, h in pairs]}]


def test_all_grounded_is_high():
    assert recompute_estimate_totals(_modules(("A", 40), ("B", 24))) == {
        "total_engineer_hours": 64.0,
        "total_engineer_days": 8,
        "grounded_task_ratio": 1.0,
        "confidence": "high",
    }


def test_mixed_is_medium():
    t = recompute_estimate_totals(_modules(("A", 40), ("B", None)))
    assert t["confidence"] == "medium"
    assert t["grounded_task_ratio"] == 0.5
    assert t["total_engineer_hours"] == 40.0
    assert t["total_engineer_days"] == 5


def test_none_grounded_is_low():
    t = recompute_estimate_totals(_modules(("A", None), ("B", None)))
    assert t == {
        "total_engineer_hours": 0.0,
        "total_engineer_days": 0,
        "grounded_task_ratio": 0.0,
        "confidence": "low",
    }


def test_filling_a_missing_task_raises_confidence_and_totals():
    # Gate-2 scenario: a task starts ungrounded, the human fills it → high.
    before = recompute_estimate_totals(_modules(("A", 40), ("B", None)))
    after = recompute_estimate_totals(_modules(("A", 40), ("B", 24)))
    assert before["confidence"] == "medium" and after["confidence"] == "high"
    assert after["total_engineer_days"] == 8 > before["total_engineer_days"]


def test_build_estimate_grafts_hours_and_delegates_totals():
    est = build_estimate(
        [{"name": "M", "tasks": [{"name": "A", "description": "a"}, {"name": "B", "description": "b"}]}],
        [{"module": "M", "task": "A", "estimated_hours": 40, "has_match": True, "reliability": 0.9}],
    )
    assert est["total_engineer_hours"] == 40.0
    assert est["grounded_task_ratio"] == 0.5
    assert est["confidence"] == "medium"
    tasks = est["modules"][0]["tasks"]
    assert tasks[0]["estimated_hours"] == 40 and tasks[0]["has_match"] is True
    assert tasks[1]["estimated_hours"] is None and tasks[1]["has_match"] is False
