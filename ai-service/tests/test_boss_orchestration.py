"""Tests for the Boss orchestrator — accept / iterate / synthesize routes."""

from __future__ import annotations

from app.domain.schemas.critic import CriticFeedback, CriticIssue
from app.domain.schemas.estimation import EstimationResult
from app.generation.agentic.boss import Boss


def _result(total: int = 25_000, summary: str = "Mid-size build description.") -> EstimationResult:
    return EstimationResult(
        summary=summary,
        confidence_pct=70,
        phases=[
            {"name": "Discovery", "duration_weeks": 1, "cost_eur": 5_000,
             "summary": "Workshops and tech spike for the project."},
            {"name": "Build", "duration_weeks": 5, "cost_eur": total - 5_000,
             "summary": "Core build of the agreed scope."},
        ],
        total_duration_weeks=6,
        total_cost_eur=total,
    )


def _critical_feedback(category: str = "math_error") -> CriticFeedback:
    return CriticFeedback(
        verdict="needs_iteration",
        issues=[
            CriticIssue(
                category=category,  # type: ignore[arg-type]
                severity="critical",
                field_path="total_cost_eur",
                description="sum mismatch",
                suggested_fix="recompute totals",
            )
        ],
        confidence_in_review=80,
    )


def test_accept_first_pass_stops_after_one_iteration() -> None:
    actor_calls: list[CriticFeedback | None] = []

    def actor(feedback):
        actor_calls.append(feedback)
        return _result()

    def critic(_result):
        return CriticFeedback(verdict="accept", issues=[], confidence_in_review=90)

    boss = Boss(max_iterations=2)
    result, trace = boss.run(actor=actor, critic=critic)

    assert result.total_cost_eur == 25_000
    assert trace.final_decision == "accept"
    assert trace.iterations_run == 1
    assert actor_calls == [None]


def test_iterate_then_accept_runs_actor_twice_with_feedback() -> None:
    actor_calls: list[CriticFeedback | None] = []

    def actor(feedback):
        actor_calls.append(feedback)
        return _result(total=25_000 + len(actor_calls))

    critic_outputs = [
        _critical_feedback(),
        CriticFeedback(verdict="accept", issues=[], confidence_in_review=95),
    ]

    def critic(_result):
        return critic_outputs.pop(0)

    boss = Boss(max_iterations=3)
    _result_out, trace = boss.run(actor=actor, critic=critic)

    assert trace.final_decision == "accept"
    assert trace.iterations_run == 2
    # First call: no feedback. Second call: receives the critic feedback.
    assert actor_calls[0] is None
    assert isinstance(actor_calls[1], CriticFeedback)
    assert actor_calls[1].issues[0].field_path == "total_cost_eur"


def test_synthesize_after_max_iterations_keeps_actor_draft_with_caveats() -> None:
    def actor(feedback):
        return _result()

    def critic(_result):
        return _critical_feedback()

    boss = Boss(max_iterations=2)
    result, trace = boss.run(actor=actor, critic=critic)

    assert trace.final_decision == "synthesize"
    assert trace.iterations_run == 2
    # Actor's numbers are preserved — no zeroed-out envelope.
    assert result.total_cost_eur == 25_000
    assert result.total_duration_weeks == 6
    assert [p.name for p in result.phases] == ["Discovery", "Build"]
    # Summary leads with the caveats block; confidence is floored at 30.
    assert result.summary.startswith("⚠ Open caveats")
    assert "[critical] math_error" in result.summary
    assert result.confidence_pct == 35  # max(30, 70 // 2)
    assert not result.summary.startswith("Out of scope:")


def test_reject_skips_to_synthesize_immediately() -> None:
    actor_calls = 0

    def actor(feedback):
        nonlocal actor_calls
        actor_calls += 1
        return _result()

    def critic(_result):
        return CriticFeedback(
            verdict="reject",
            issues=[
                CriticIssue(
                    category="scope_mismatch",
                    severity="critical",
                    field_path="summary",
                    description="user asked for mobile but got web",
                )
            ],
            confidence_in_review=85,
        )

    boss = Boss(max_iterations=4)
    result, trace = boss.run(actor=actor, critic=critic)

    assert actor_calls == 1  # reject shortcuts the loop
    assert trace.final_decision == "synthesize"
    # Caveats are still surfaced via the new fallback policy.
    assert "scope_mismatch" in result.summary
    assert result.summary.startswith("⚠ Open caveats")
    # The actor's draft survives reject too — UX is more important than
    # ceremonially zeroing things out.
    assert result.total_cost_eur == 25_000


def test_invalid_max_iterations_rejected_at_construction() -> None:
    import pytest

    with pytest.raises(ValueError):
        Boss(max_iterations=0)


def test_trace_records_one_entry_per_iteration() -> None:
    actor_calls = 0

    def actor(feedback):
        nonlocal actor_calls
        actor_calls += 1
        return _result()

    critic_outputs = [
        _critical_feedback(),
        _critical_feedback(category="phase_imbalance"),
        CriticFeedback(verdict="accept", issues=[], confidence_in_review=90),
    ]

    def critic(_result):
        return critic_outputs.pop(0)

    boss = Boss(max_iterations=4)
    _result_out, trace = boss.run(actor=actor, critic=critic)

    assert len(trace.iterations) == 3
    assert [it.decision_after for it in trace.iterations] == ["iterate", "iterate", "accept"]
    assert "math_error" in trace.iterations[0].issue_summary[0]
    assert "phase_imbalance" in trace.iterations[1].issue_summary[0]
