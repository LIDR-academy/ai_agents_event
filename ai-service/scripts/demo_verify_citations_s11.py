#!/usr/bin/env python3
"""Offline demo of Session 11 line-level citation verification.

Builds a realistic grounded :class:`Estimate` from a small set of "retrieved"
chunks, deliberately plants ONE dangling citation (an id the LLM never saw) and
one line with no sufficient source data, then runs :func:`verify_citations` and
prints the :class:`CitationReport`. No network, no database — it exercises the
verification logic alone, so it doubles as the acceptance-criteria proof:

* every grounded line cites at least one real chunk,
* the verifier flags the planted dangling citation,
* the unsupported line is reported as "insufficient", not back-filled.

Usage::

    uv run python scripts/demo_verify_citations_s11.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.generation.rag.schemas import (  # noqa: E402
    Assumption,
    CitationReport,
    Estimate,
    RetrievedChunk,
    SourceReference,
    TaskItem,
    WorkModule,
)
from app.generation.rag.validation import verify_citations  # noqa: E402


def _retrieved() -> list[RetrievedChunk]:
    """The chunks the generator was actually given (ids 101, 102, 103)."""
    return [
        RetrievedChunk(
            id=101,
            content="BUD-2024-001 :: AUTH-001 OAuth 2.0 authentication backend — 120 h",
            sector="finance",
            project_year=2024,
            chunk_type="budget_component",
            distance=0.21,
            source_id="BUD-2024-001",
            budget_id="BUD-2024-001",
        ),
        RetrievedChunk(
            id=102,
            content="BUD-2024-001 :: PSD2-002 PSD2 open banking connectors — 160 h",
            sector="finance",
            project_year=2024,
            chunk_type="budget_component",
            distance=0.27,
            source_id="BUD-2024-001",
            budget_id="BUD-2024-001",
        ),
        RetrievedChunk(
            id=103,
            content="BUD-2024-001 :: TXN-003 Transaction ledger service — 140 h",
            sector="finance",
            project_year=2024,
            chunk_type="budget_component",
            distance=0.31,
            source_id="BUD-2024-001",
            budget_id="BUD-2024-001",
        ),
    ]


def _estimate() -> Estimate:
    """A grounded estimate with one planted dangling citation + one no-data line."""
    return Estimate(
        total_engineer_days=53,
        duration_weeks=11,
        modules=[
            WorkModule(
                name="Authentication & SCA",
                tasks=[
                    TaskItem(
                        name="OAuth 2.0 backend",
                        engineer_days=15,
                        grounded=True,
                        sources=[
                            SourceReference(
                                chunk_id="101",
                                document_id="BUD-2024-001",
                                evidence="AUTH-001 OAuth 2.0 authentication backend — 120 h",
                            )
                        ],
                    ),
                ],
            ),
            WorkModule(
                name="PSD2 & Open Banking",
                tasks=[
                    TaskItem(
                        name="Open banking connectors",
                        engineer_days=20,
                        grounded=True,
                        sources=[
                            SourceReference(
                                chunk_id="102",
                                document_id="BUD-2024-001",
                                evidence="PSD2-002 PSD2 open banking connectors — 160 h",
                            )
                        ],
                    ),
                ],
            ),
            WorkModule(
                name="Ledger",
                tasks=[
                    # Planted DANGLING citation: chunk 999 was never retrieved.
                    TaskItem(
                        name="Transaction ledger",
                        engineer_days=18,
                        grounded=True,
                        sources=[
                            SourceReference(
                                chunk_id="999",
                                document_id="BUD-2024-001",
                                evidence="TXN-003 Transaction ledger service — 140 h",
                            )
                        ],
                    ),
                ],
            ),
            WorkModule(
                name="Reporting",
                tasks=[
                    # No sufficient source data: flagged, not back-filled with hours.
                    TaskItem(name="Regulatory reporting", grounded=False),
                ],
            ),
        ],
        sources=[],
        assumptions=[
            Assumption(
                description="Regulatory reporting has no historical analog in the context.",
                impact="medium",
                rationale="No retrieved budget covers regulatory reporting.",
            )
        ],
        confidence="high",
        reasoning="Derived from the retrieved BUD-2024-001 components.",
    )


def _print_report(report: CitationReport) -> None:
    print("=== Citation verification report ===")
    print(
        f"lines: {report.total_lines}  grounded: {report.grounded_lines}  "
        f"dangling: {report.dangling_lines}  insufficient: {report.insufficient_lines}"
    )
    print(f"verified citations: {report.verified_citations}")
    print(f"dangling citations: {report.dangling_citations or '(none)'}")
    print()
    print(f"{'module':<24} {'line':<24} {'status':<13} cited -> dangling")
    print("-" * 88)
    for line in report.lines:
        print(
            f"{line.module:<24} {line.component:<24} {line.status:<13} "
            f"{line.cited_chunk_ids} -> {line.dangling_chunk_ids or '-'}"
        )


def main() -> int:
    chunks = _retrieved()
    estimate = _estimate()
    retrieved_ids = {str(chunk.id) for chunk in chunks}
    report = verify_citations(estimate, retrieved_ids)
    _print_report(report)

    # Acceptance checks (exit non-zero if any contract is violated).
    ok = (
        report.dangling_citations == ["999"]
        and report.dangling_lines == 1
        and report.grounded_lines == 2
        and report.insufficient_lines == 1
    )
    print()
    print("ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
