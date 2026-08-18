"""Post-generation checks for grounded estimates (Sessions 9 & 11).

Two independent guards run after the LLM returns an :class:`Estimate`:

* :func:`verify_citations` (Session 11) — line-level, programmatic citation
  verification. Every cited ``chunk_id`` (per-line :class:`SourceReference` and
  the estimate-global :class:`SourceCitation`) must resolve to a chunk that was
  actually retrieved. Ids that were never in the context are *dangling*: a
  hallucination wearing the costume of rigor. The returned
  :class:`CitationReport` separates correctly grounded lines, dangling
  citations and lines explicitly marked as having no sufficient source data.
* :func:`check_coherence` — the ``insufficient`` confidence level has a strict
  shape (no numbers, an explanation present); a violation is a malformed
  response, not a valid estimate.
"""

from __future__ import annotations

from app.generation.rag.schemas import (
    CitationReport,
    Estimate,
    LineCitation,
    RetrievedChunk,
)


def verify_citations(
    estimate: Estimate,
    retrieved_chunk_ids: set[str],
) -> CitationReport:
    """Verify every citation against the chunks actually handed to the LLM.

    Walks each estimate line (``modules[].tasks[]``) and checks that every
    ``chunk_id`` it cites is present in ``retrieved_chunk_ids``. The
    estimate-global ``sources`` (:class:`SourceCitation`) are checked too, so a
    fabricated id cannot hide at either level.

    Parameters
    ----------
    estimate:
        The generated estimate to audit.
    retrieved_chunk_ids:
        The ids of the chunks that were actually placed in the context block
        (typically ``{str(chunk.id) for chunk in kept}``).

    Returns
    -------
    CitationReport
        Per-line statuses plus aggregate counts. ``dangling_citations`` is the
        sorted, de-duplicated set of cited ids that were never retrieved; an
        empty list means every citation is real.
    """
    lines: list[LineCitation] = []
    verified = 0
    dangling: set[str] = set()
    grounded_lines = dangling_lines = insufficient_lines = 0

    for module in estimate.modules:
        for task in module.tasks:
            cited = [ref.chunk_id for ref in task.sources]
            line_dangling = [cid for cid in cited if cid not in retrieved_chunk_ids]
            verified += len(cited) - len(line_dangling)
            dangling.update(line_dangling)

            if not task.grounded:
                status = "insufficient"
                insufficient_lines += 1
            elif line_dangling:
                status = "dangling"
                dangling_lines += 1
            else:
                status = "grounded"
                grounded_lines += 1

            lines.append(
                LineCitation(
                    module=module.name,
                    component=task.name,
                    status=status,
                    cited_chunk_ids=cited,
                    dangling_chunk_ids=line_dangling,
                )
            )

    # Estimate-global citations (the coarse Session 9 layer) are verified too:
    # a fabricated id there is just as much a grounding failure.
    for citation in estimate.sources:
        cid = str(citation.source_id)
        if cid in retrieved_chunk_ids:
            verified += 1
        else:
            dangling.add(cid)

    return CitationReport(
        total_lines=len(lines),
        grounded_lines=grounded_lines,
        dangling_lines=dangling_lines,
        insufficient_lines=insufficient_lines,
        verified_citations=verified,
        dangling_citations=sorted(dangling),
        lines=lines,
    )


def verify_citations_for_chunks(
    estimate: Estimate,
    retrieved_chunks: list[RetrievedChunk],
) -> CitationReport:
    """Convenience wrapper: derive the id set from the retrieved chunks."""
    return verify_citations(estimate, {str(chunk.id) for chunk in retrieved_chunks})


def check_coherence(estimate: Estimate) -> bool:
    """Return whether the estimate's confidence level matches its content.

    When ``confidence == "insufficient"``: both numeric totals must be ``None``,
    ``modules`` must be empty, and ``insufficient_context_explanation`` must be
    non-empty. Any other confidence level is always considered coherent here
    (the numeric checks belong to the schema/business rules, not to this guard).
    """
    if estimate.confidence != "insufficient":
        return True
    return (
        estimate.total_engineer_days is None
        and estimate.duration_weeks is None
        and not estimate.modules
        and bool(estimate.insufficient_context_explanation)
    )
