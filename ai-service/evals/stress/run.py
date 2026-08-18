"""Session 6 stress test runner — empirically map where the CAG breaks.

Orchestrates ``scenarios × attachment_sizes × repeats`` against a running
estimator (``--http`` mode) or an in-process ``TestClient`` (for smoke
tests). For each turn it:

1. POSTs ``/sessions/{id}/estimate`` with the turn's transcript and the
   pre-built PDF attachment (or none, for size 0).
2. Reads ``response.observation`` (added in Bloque 1) — no log parsing.
3. GETs ``/sessions/{id}`` to obtain the post-turn session snapshot for
   ``MemoryDriftMetric``.
4. Writes one CSV row with all telemetry + three boolean metric verdicts.

The deliverable is the CSV — the alumno reads it and writes ``REPORT.md``
with the lectures. The runner does not generate the report.

Run with::

    uv run python -m evals.stress.run \\
        --http http://localhost:8000 \\
        --scenarios growing,pivot,contradiction \\
        --attachment-sizes 0,5,20,50,100 \\
        --repeats 3 \\
        --output evals/stress/results.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from app.domain.schemas.estimation import TurnObservation
from evals.stress.metrics import (
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
)
from evals.stress.scenarios import Scenario, ScenarioTurn, get_scenario


_FIXTURES_DIR = Path(__file__).parent / "fixtures"


# -- transport ---------------------------------------------------------------


@contextmanager
def _open_client(http_base_url: str | None) -> Iterator[httpx.Client | Any]:
    """Yield a sync client. ``--http`` → real httpx; otherwise ``TestClient``.

    ``TestClient`` is imported lazily so the import cost is only paid in
    smoke mode — and so the runner does not require fastapi at import time
    in environments that only need ``--http`` (e.g. running from a CI box
    pointing at a remote service).
    """
    if http_base_url:
        with httpx.Client(base_url=http_base_url, timeout=180.0) as client:
            yield client
        return

    # In-process: TestClient + isolated SessionStore so dev sessions don't leak.
    from fastapi.testclient import TestClient

    from app.dependencies import get_session_store
    from app.main import app
    from app.generation.conversation.store import SessionStore

    store = SessionStore(max_turns=6)
    app.dependency_overrides[get_session_store] = lambda: store
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session_store, None)


# -- per-turn execution ------------------------------------------------------


def _form_body(scenario: Scenario, turn: ScenarioTurn) -> dict[str, str]:
    return {
        "transcript": turn.transcript,
        "project_type": scenario.project_type.value,
        "detail_level": scenario.detail_level.value,
        "output_format": scenario.output_format.value,
    }


def _attachment_files(size_kb: int) -> list[tuple[str, tuple[str, bytes, str]]] | None:
    if size_kb == 0:
        return None
    path = _FIXTURES_DIR / f"attach_{size_kb}kb.pdf"
    if not path.exists():
        raise FileNotFoundError(
            f"Fixture {path} missing — run "
            "`uv run python -m evals.stress.fixtures.build_pdfs` first."
        )
    return [("attachments", (path.name, path.read_bytes(), "application/pdf"))]


def _execute_turn(
    client: httpx.Client,
    session_id: str,
    scenario: Scenario,
    turn: ScenarioTurn,
    files: list | None,
) -> tuple[TurnObservation, dict[str, Any], int]:
    """One POST estimate + one GET snapshot.

    Returns ``(observation, snapshot, wall_clock_ms)``. The wall-clock time
    includes the round-trip; ``observation.latency_ms`` is the LLM-only
    latency measured server-side. Both are useful: the gap is network + the
    extractor + the second metadata-extraction call.
    """
    t0 = time.perf_counter()
    response = client.post(
        f"/sessions/{session_id}/estimate",
        data=_form_body(scenario, turn),
        files=files,
    )
    wall_ms = int((time.perf_counter() - t0) * 1000)
    response.raise_for_status()
    payload = response.json()

    observation_dict = payload.get("observation")
    if observation_dict is None:
        raise RuntimeError(
            "response.observation is missing — did Bloque 1 ship in this "
            "estimator? Rebuild the docker image."
        )
    observation = TurnObservation(**observation_dict)

    snapshot_response = client.get(f"/sessions/{session_id}")
    snapshot_response.raise_for_status()
    snapshot = snapshot_response.json()

    return observation, snapshot, wall_ms


# -- main loop ---------------------------------------------------------------


_CSV_COLUMNS = [
    "scenario",
    "attachment_size_kb",
    "repeat",
    "turn_index",
    "session_id",
    "enriched_transcript_chars",
    "attachments_total_chars",
    "messages_in_window",
    "anchors_count",
    "summary_chars",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "wall_clock_ms",
    "cache_hit_kind",
    "last_resolved_tier",
    "latency_budget_passed",
    "cost_budget_passed",
    "memory_drift_passed",
    "tracked_fact",
    "error",
]


def _run_one_session(
    client: httpx.Client,
    scenario: Scenario,
    size_kb: int,
    repeat: int,
    latency_metric: LatencyBudgetMetric,
    cost_metric: CostBudgetMetric,
    writer: csv.DictWriter,
) -> int:
    """Walk the full scenario once. Returns rows written."""
    files = _attachment_files(size_kb)

    create_resp = client.post("/sessions")
    create_resp.raise_for_status()
    session_id = create_resp.json()["session_id"]

    # Track the turn-1 anchor fact across the whole session. That's the
    # canonical "is the project_name from the start still alive at turn N?"
    # measurement. Additional facts could be threaded with the same pattern.
    tracked_fact = scenario.turns[0].fact_introduced
    tracked_field = scenario.turns[0].fact_field
    drift_metric = (
        MemoryDriftMetric(fact=tracked_fact, fact_field=tracked_field)
        if tracked_fact
        else None
    )

    rows_written = 0
    for turn in scenario.turns:
        row: dict[str, Any] = {
            "scenario": scenario.name,
            "attachment_size_kb": size_kb,
            "repeat": repeat,
            "tracked_fact": tracked_fact or "",
        }
        try:
            observation, snapshot, wall_ms = _execute_turn(
                client, session_id, scenario, turn, files
            )
        except Exception as exc:  # noqa: BLE001
            row.update(
                {
                    "turn_index": "",
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
            )
            writer.writerow(row)
            rows_written += 1
            # Aborting the rest of this session — a 5xx mid-conversation
            # would skew memory drift for later turns.
            break

        latency_pass = latency_metric.evaluate(observation).passed
        cost_pass = cost_metric.evaluate(observation).passed
        if drift_metric is None or observation.turn_index == 1:
            # Turn 1 trivially "remembers" itself; the fact is whatever it
            # just stated. Skip the metric for that row (NaN-as-empty).
            drift_pass = ""
        else:
            drift_pass = bool(drift_metric.evaluate(snapshot).passed)

        row.update(
            {
                **observation.model_dump(),
                "wall_clock_ms": wall_ms,
                "latency_budget_passed": latency_pass,
                "cost_budget_passed": cost_pass,
                "memory_drift_passed": drift_pass,
                "error": "",
            }
        )
        writer.writerow(row)
        rows_written += 1

    return rows_written


def _print_summary(csv_path: Path) -> None:
    """Reload the CSV and print P50/P95 + means per (scenario, size)."""
    by_group: dict[tuple[str, str], list[dict[str, str]]] = {}
    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("error"):
                continue
            key = (row["scenario"], row["attachment_size_kb"])
            by_group.setdefault(key, []).append(row)

    print("\nSummary (per scenario × attachment size)")
    header = f"{'scenario':<14} {'kb':>4} {'n':>4} {'P50 ms':>8} {'P95 ms':>8} {'tot $':>10} {'drift%':>7}"
    print(header)
    print("-" * len(header))
    for (scenario, size_kb), rows in sorted(by_group.items()):
        latencies = sorted(int(r["latency_ms"]) for r in rows)
        costs = [float(r["cost_usd"]) for r in rows]
        drifts = [r["memory_drift_passed"] for r in rows if r["memory_drift_passed"] != ""]
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95_idx = max(0, int(len(latencies) * 0.95) - 1)
        p95 = latencies[p95_idx] if latencies else 0
        total_cost = sum(costs)
        drift_pct = (
            100.0 * sum(1 for d in drifts if d == "True") / len(drifts) if drifts else 0.0
        )
        print(
            f"{scenario:<14} {size_kb:>4} {len(rows):>4} "
            f"{p50:>8} {p95:>8} {total_cost:>10.4f} {drift_pct:>6.1f}%"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--http",
        default=None,
        help="Base URL of a running estimator (e.g. http://localhost:8000). "
        "If omitted, the runner uses an in-process TestClient (smoke only).",
    )
    parser.add_argument(
        "--scenarios",
        default="growing,pivot,contradiction",
        help="Comma-separated scenario names.",
    )
    parser.add_argument(
        "--attachment-sizes",
        default="0,5,20,50,100",
        help="Comma-separated PDF sizes in KB. 0 = no attachment.",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--latency-budget-ms", type=int, default=8000)
    parser.add_argument("--cost-budget-usd", type=float, default=0.02)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/stress/results.csv"),
        help="CSV path (relative to current working directory).",
    )
    args = parser.parse_args()

    scenarios = [get_scenario(name.strip()) for name in args.scenarios.split(",")]
    sizes = [int(s) for s in args.attachment_sizes.split(",")]

    # Fail early if any fixture is missing — cheaper than discovering it
    # mid-run after a 30-minute LLM marathon.
    for kb in sizes:
        if kb != 0:
            _attachment_files(kb)

    latency_metric = LatencyBudgetMetric(budget_ms=args.latency_budget_ms)
    cost_metric = CostBudgetMetric(budget_usd=args.cost_budget_usd)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"Stress run: {len(scenarios)} scenarios × {len(sizes)} sizes × "
        f"{args.repeats} repeats × up to 20 turns each"
    )
    total_rows = 0
    t_start = time.perf_counter()
    with args.output.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        with _open_client(args.http) as client:
            for scenario in scenarios:
                for size_kb in sizes:
                    for repeat in range(args.repeats):
                        print(
                            f"  → {scenario.name:<14} kb={size_kb:>3} "
                            f"repeat={repeat + 1}/{args.repeats}"
                        )
                        rows = _run_one_session(
                            client,
                            scenario,
                            size_kb,
                            repeat,
                            latency_metric,
                            cost_metric,
                            writer,
                        )
                        total_rows += rows
                        fh.flush()

    elapsed_s = int(time.perf_counter() - t_start)
    print(f"\nWrote {total_rows} rows to {args.output} in {elapsed_s}s")
    _print_summary(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
