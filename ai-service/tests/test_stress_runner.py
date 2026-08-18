"""Smoke test of the stress runner against an in-process fake LLM.

The full runner orchestration (scenarios × sizes × repeats + CSV writer)
is exercised end-to-end with a FakeLLMWrapper, so that wiring bugs in the
``observation`` plumbing, the CSV schema, or the metric calls surface in
seconds without burning real LLM credit.
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.domain.schemas.estimation import DetailLevel, OutputFormat, ProjectType
from evals.stress.metrics import CostBudgetMetric, LatencyBudgetMetric
from evals.stress.run import _CSV_COLUMNS, _run_one_session
from evals.stress.scenarios import Scenario, ScenarioTurn


def _tiny_scenario() -> Scenario:
    """A scenario short enough to keep tests fast but long enough to exercise
    the sliding window + at least one drift evaluation post-turn-1."""

    # Build 20 minimally valid turns (the scenario dataclass enforces >=20).
    turns = [
        ScenarioTurn(
            transcript=(
                "We want a B2B SaaS called Nimbus for procurement teams. "
                "Stack: React + Postgres. Team of 3."
            ),
            fact_introduced="Nimbus",
            fact_field="project_name",
        )
    ]
    for i in range(2, 21):
        turns.append(
            ScenarioTurn(
                transcript=(
                    f"Turn {i}: add another feature, expand the scope a little. "
                    "Keep the same stack and team for now."
                ),
                fact_introduced=None,
            )
        )
    return Scenario(
        name="growing",
        project_type=ProjectType.WEB_SAAS,
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
        turns=turns,
    )


def test_runner_writes_csv_with_expected_columns_and_rows(
    conversational_client, tmp_path: Path
):
    """Walk 3 turns of a scripted session, validate CSV shape."""
    client, _store = conversational_client
    scenario = _tiny_scenario()
    # Truncate to 3 turns for speed — the scenario object enforces >=20 but
    # _run_one_session iterates whatever we give it via scenario.turns.
    object.__setattr__(scenario, "turns", scenario.turns[:3])

    csv_path = tmp_path / "smoke.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        rows_written = _run_one_session(
            client=client,
            scenario=scenario,
            size_kb=0,
            repeat=0,
            latency_metric=LatencyBudgetMetric(budget_ms=60_000),
            cost_metric=CostBudgetMetric(budget_usd=10.0),
            writer=writer,
        )

    assert rows_written == 3
    with csv_path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    # Every column is filled (apart from a few that are intentionally blank
    # in turn 1 — memory_drift_passed, which is empty for turn_index == 1).
    for row in rows:
        assert row["scenario"] == "growing"
        assert row["attachment_size_kb"] == "0"
        assert row["tracked_fact"] == "Nimbus"
        assert row["error"] == ""
        assert int(row["turn_index"]) >= 1
        assert int(row["messages_in_window"]) > 0
        assert row["latency_budget_passed"] == "True"
        assert row["cost_budget_passed"] == "True"

    # Turn 1 → drift column blank (trivial); turns 2 & 3 → must be filled.
    assert rows[0]["memory_drift_passed"] == ""
    assert rows[1]["memory_drift_passed"] in {"True", "False"}
    assert rows[2]["memory_drift_passed"] in {"True", "False"}


def test_runner_records_error_row_on_failure(conversational_client, tmp_path: Path):
    """If a turn raises, the runner records an error row and stops the session."""
    client, _store = conversational_client
    scenario = _tiny_scenario()
    # Replace turn 2's transcript with one short enough to trigger the
    # ``min_length=20`` validation on the form — that's a 422 from the API.
    bad_turn = ScenarioTurn(transcript="too short", fact_introduced=None)
    object.__setattr__(scenario, "turns", [scenario.turns[0], bad_turn, scenario.turns[2]])

    csv_path = tmp_path / "smoke.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        rows_written = _run_one_session(
            client=client,
            scenario=scenario,
            size_kb=0,
            repeat=0,
            latency_metric=LatencyBudgetMetric(budget_ms=60_000),
            cost_metric=CostBudgetMetric(budget_usd=10.0),
            writer=writer,
        )

    assert rows_written == 2  # turn 1 ok + turn 2 errored, then aborted
    with csv_path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["error"] == ""
    assert rows[1]["error"] != ""
    assert "HTTPStatusError" in rows[1]["error"] or "422" in rows[1]["error"]
