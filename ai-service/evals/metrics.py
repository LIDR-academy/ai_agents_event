"""Deterministic metrics over a ``(GoldenCase, EstimationResult)`` pair.

The point of keeping these in-tree (rather than offloading to DeepEval LLM
judges by default) is that the suite runs without an LLM and tells us
exactly *why* a case failed. DeepEval's ``GEval`` is wired as an optional
``LLMJudgeMetric`` you can turn on with ``--llm-judge`` if you want a
qualitative second opinion.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.schemas.estimation import EstimationResult, OUT_OF_SCOPE_PREFIX
from evals.dataset import GoldenCase


@dataclass
class MetricResult:
    name: str
    score: float
    passed: bool
    details: str


class SchemaAdherenceMetric:
    """The actor's structured output and the validators are already enforced
    by Instructor; this metric exists to flag if any of those guarantees
    *regress* (e.g. a future change drops a validator). It also checks the
    phase count against the expected range, when provided."""

    name = "schema_adherence"

    def evaluate(self, case: GoldenCase, result: EstimationResult) -> MetricResult:
        problems: list[str] = []

        phase_sum = sum(p.cost_eur for p in result.phases)
        if phase_sum != result.total_cost_eur:
            problems.append(
                f"phases sum ({phase_sum}) != total_cost_eur ({result.total_cost_eur})"
            )

        if (
            result.confidence_pct < 30
            and not result.summary.startswith(OUT_OF_SCOPE_PREFIX)
        ):
            problems.append("low confidence missing 'Out of scope:' prefix")

        if case.expected_phase_count_range:
            lo, hi = case.expected_phase_count_range
            if not (lo <= len(result.phases) <= hi):
                problems.append(
                    f"phase count {len(result.phases)} outside [{lo}, {hi}]"
                )

        score = 1.0 if not problems else 0.0
        return MetricResult(
            name=self.name,
            score=score,
            passed=not problems,
            details="; ".join(problems) or "schema ok",
        )


class CostBoundsMetric:
    """Sanity bound for absolute cost and duration. Out-of-scope cases
    expect cost=0/duration=1 (the placeholder envelope)."""

    name = "cost_bounds"

    def evaluate(self, case: GoldenCase, result: EstimationResult) -> MetricResult:
        problems: list[str] = []

        if case.expected_out_of_scope:
            if result.total_cost_eur != 0 or result.total_duration_weeks != 1:
                problems.append(
                    "expected out-of-scope envelope (cost=0, duration=1), got "
                    f"cost={result.total_cost_eur}, weeks={result.total_duration_weeks}"
                )
        else:
            if case.expected_cost_range_eur:
                lo, hi = case.expected_cost_range_eur
                if not (lo <= result.total_cost_eur <= hi):
                    problems.append(
                        f"cost {result.total_cost_eur} EUR outside [{lo}, {hi}]"
                    )
            if case.expected_duration_weeks_range:
                lo, hi = case.expected_duration_weeks_range
                if not (lo <= result.total_duration_weeks <= hi):
                    problems.append(
                        f"duration {result.total_duration_weeks}w outside [{lo}, {hi}]"
                    )

        score = 1.0 if not problems else 0.0
        return MetricResult(
            name=self.name,
            score=score,
            passed=not problems,
            details="; ".join(problems) or "cost & duration within bounds",
        )


class ContentRecallMetric:
    """Lightweight recall — does the summary / phase prose mention the
    things the user actually cared about? Out-of-scope cases skip this."""

    name = "content_recall"

    def evaluate(self, case: GoldenCase, result: EstimationResult) -> MetricResult:
        if case.expected_out_of_scope:
            return MetricResult(
                name=self.name,
                score=1.0,
                passed=True,
                details="skipped — case is out-of-scope by design",
            )

        haystack = " ".join(
            [result.summary] + [phase.summary + " " + phase.name for phase in result.phases]
        ).lower()

        missing_summary = [
            term for term in case.expected_in_summary if term.lower() not in haystack
        ]

        tech_hit = True
        if case.expected_technologies_any_of:
            tech_hit = any(t.lower() in haystack for t in case.expected_technologies_any_of)

        problems: list[str] = []
        if missing_summary:
            problems.append(f"missing in summary: {missing_summary}")
        if not tech_hit:
            problems.append(
                f"none of {case.expected_technologies_any_of} mentioned anywhere"
            )

        score = 1.0 if not problems else 0.0
        return MetricResult(
            name=self.name,
            score=score,
            passed=not problems,
            details="; ".join(problems) or "expected content present",
        )


_DEFAULT_METRICS = (SchemaAdherenceMetric(), CostBoundsMetric(), ContentRecallMetric())


def run_all_metrics(
    case: GoldenCase, result: EstimationResult
) -> list[MetricResult]:
    return [m.evaluate(case, result) for m in _DEFAULT_METRICS]
