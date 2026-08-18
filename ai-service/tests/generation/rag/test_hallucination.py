"""Unit tests for the Session 11 semantic hallucination gate.

The judge half (an LLM call) is not exercised here; the tests drive the
deterministic anchor and the pure ``gate_line`` combiner (``use_judge=False``),
so they run offline with no network.
"""

from __future__ import annotations

import asyncio

from app.generation.rag.quality.hallucination import (
    _chunk_hours,
    gate_estimate,
    gate_line,
    numeric_anchor,
)
from app.generation.rag.schemas import (
    Estimate,
    LineVerdict,
    RetrievedChunk,
    SourceReference,
    TaskItem,
    WorkModule,
)


def _chunk(cid: int, content: str, hours: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        id=cid,
        content=content,
        chunk_type="budget_component",
        distance=0.2,
        source_id="BUD-1",
        budget_id="BUD-1",
        estimated_hours=hours,
    )


def _task(name: str, days: int | None, grounded: bool, cited: str | None = "101") -> TaskItem:
    sources = (
        [SourceReference(chunk_id=cited, document_id="BUD-1", evidence="120 h")]
        if grounded and cited
        else []
    )
    return TaskItem(name=name, engineer_days=days, grounded=grounded, sources=sources)


def test_chunk_hours_prefers_metadata_then_parses_text():
    assert _chunk_hours(_chunk(1, "no figure here", hours=120)) == 120.0
    assert _chunk_hours(_chunk(1, "AUTH :: OAuth backend — 160 h")) == 160.0
    assert _chunk_hours(_chunk(1, "no hours at all")) is None


def test_numeric_anchor_sums_cited_hours_in_days():
    chunks = {"101": _chunk(101, "x", hours=120)}
    anchor = numeric_anchor(_task("t", 10, grounded=True), chunks)
    assert anchor == 120 / 8  # 15 engineer-days


def test_gate_line_grounded_when_within_tolerance():
    gate = gate_line(
        _task("ok", 10, grounded=True), "Auth", anchor_days=15.0, verdict=None, tolerance=0.5
    )
    assert gate.status == "grounded"


def test_gate_line_degraded_on_numeric_overshoot():
    # Claims 90d against a 15d anchor → far over tolerance.
    gate = gate_line(
        _task("inflated", 90, grounded=True), "Auth", anchor_days=15.0, verdict=None, tolerance=0.5
    )
    assert gate.status == "degraded"
    assert gate.numeric_deviation and gate.numeric_deviation > 0.5


def test_gate_line_insufficient_when_ungrounded():
    gate = gate_line(
        _task("nodata", None, grounded=False, cited=None),
        "Auth",
        anchor_days=None,
        verdict=None,
        tolerance=0.5,
    )
    assert gate.status == "insufficient"


def test_gate_line_degraded_when_judge_rejects():
    verdict = LineVerdict(
        module="Auth", component="ok", entailed=False, reason="evidence is unrelated"
    )
    gate = gate_line(
        _task("ok", 10, grounded=True), "Auth", anchor_days=15.0, verdict=verdict, tolerance=0.5
    )
    assert gate.status == "degraded"
    assert "unrelated" in gate.reason


def test_gate_estimate_aggregates_without_judge():
    chunks = [_chunk(101, "AUTH :: OAuth backend — 120 h", hours=120)]
    estimate = Estimate(
        confidence="high",
        reasoning="r",
        modules=[
            WorkModule(
                name="Auth",
                tasks=[
                    _task("ok line", 10, grounded=True),
                    _task("inflated line", 90, grounded=True),
                    _task("no data", None, grounded=False, cited=None),
                ],
            )
        ],
    )
    report = asyncio.run(
        gate_estimate(estimate, chunks, tolerance=0.5, judge_model="gpt-5-mini", use_judge=False)
    )
    assert report.total_lines == 3
    assert report.grounded_lines == 1
    assert report.degraded_lines == 1
    assert report.insufficient_lines == 1
    assert report.has_degraded is True
