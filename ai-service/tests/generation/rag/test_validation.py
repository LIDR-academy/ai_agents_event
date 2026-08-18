"""Unit tests for post-generation validation (Sessions 9 & 11)."""

from __future__ import annotations

from app.generation.rag.schemas import (
    Estimate,
    RetrievedChunk,
    SourceCitation,
    SourceReference,
    TaskItem,
    WorkModule,
)
from app.generation.rag.validation import (
    check_coherence,
    verify_citations,
    verify_citations_for_chunks,
)


def _ref(chunk_id: str, document_id: str = "BUD-2024-001") -> SourceReference:
    return SourceReference(
        chunk_id=chunk_id, document_id=document_id, evidence="Estimated hours: 120"
    )


def _estimate(
    *,
    line_sources: list[SourceReference] | None = None,
    grounded: bool = True,
    engineer_days: int | None = 20,
    global_source_ids: list[int] | None = None,
    confidence="high",
) -> Estimate:
    task = TaskItem(
        name="Auth",
        engineer_days=engineer_days,
        grounded=grounded,
        sources=line_sources or [],
    )
    return Estimate(
        total_engineer_days=engineer_days,
        duration_weeks=4,
        modules=[WorkModule(name="Authentication", tasks=[task])],
        sources=[
            SourceCitation(source_id=sid, relevance="primary", used_for="auth")
            for sid in (global_source_ids or [])
        ],
        assumptions=[],
        confidence=confidence,
        reasoning="Derived from retrieved budgets.",
    )


def test_verify_citations_all_valid_reports_grounded():
    estimate = _estimate(line_sources=[_ref("1")], global_source_ids=[1, 2])
    report = verify_citations(estimate, {"1", "2"})
    assert report.dangling_citations == []
    assert report.has_dangling is False
    assert report.grounded_lines == 1
    assert report.dangling_lines == 0
    assert report.insufficient_lines == 0
    assert report.verified_citations == 3  # one line ref + two global citations
    assert report.lines[0].status == "grounded"


def test_verify_citations_flags_dangling_line_citation():
    # The line cites chunk 42, which was never retrieved.
    estimate = _estimate(line_sources=[_ref("42")], global_source_ids=[1])
    report = verify_citations(estimate, {"1", "2"})
    assert report.dangling_citations == ["42"]
    assert report.has_dangling is True
    assert report.dangling_lines == 1
    assert report.grounded_lines == 0
    assert report.lines[0].status == "dangling"
    assert report.lines[0].dangling_chunk_ids == ["42"]


def test_verify_citations_flags_dangling_global_citation():
    estimate = _estimate(line_sources=[_ref("1")], global_source_ids=[99])
    report = verify_citations(estimate, {"1"})
    assert report.dangling_citations == ["99"]
    assert report.grounded_lines == 1  # the line itself is clean


def test_verify_citations_marks_insufficient_line():
    estimate = _estimate(grounded=False, engineer_days=None, line_sources=[])
    report = verify_citations(estimate, {"1"})
    assert report.insufficient_lines == 1
    assert report.grounded_lines == 0
    assert report.dangling_citations == []
    assert report.lines[0].status == "insufficient"


def test_verify_citations_empty_retrieval_flags_every_cited_id():
    estimate = _estimate(line_sources=[_ref("1")], global_source_ids=[2])
    report = verify_citations(estimate, set())
    assert report.dangling_citations == ["1", "2"]


def test_verify_citations_for_chunks_wrapper_matches():
    chunk = RetrievedChunk(
        id=1,
        content="x",
        sector="finance",
        project_year=2024,
        chunk_type="budget_component",
        distance=0.3,
    )
    estimate = _estimate(line_sources=[_ref("1")])
    assert verify_citations_for_chunks(estimate, [chunk]).has_dangling is False


def test_check_coherence_insufficient_with_nulls_is_coherent():
    estimate = Estimate(
        total_engineer_days=None,
        duration_weeks=None,
        confidence="insufficient",
        reasoning="no sources",
        insufficient_context_explanation="No relevant budgets retrieved.",
    )
    assert check_coherence(estimate) is True


def test_check_coherence_insufficient_with_numbers_is_incoherent():
    estimate = Estimate(
        total_engineer_days=10,
        duration_weeks=2,
        confidence="insufficient",
        reasoning="contradiction",
        insufficient_context_explanation="",
    )
    assert check_coherence(estimate) is False


def test_check_coherence_non_insufficient_always_true():
    estimate = _estimate(line_sources=[_ref("1")], confidence="low")
    assert check_coherence(estimate) is True


# --- Session 11 schema integrity (the line-level grounding contract) --------


def test_grounded_line_requires_a_source():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskItem(name="Auth", engineer_days=10, grounded=True, sources=[])


def test_ungrounded_line_cannot_carry_hours():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskItem(name="Auth", engineer_days=10, grounded=False)


def test_ungrounded_line_cannot_carry_sources():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskItem(name="Auth", grounded=False, sources=[_ref("1")])
