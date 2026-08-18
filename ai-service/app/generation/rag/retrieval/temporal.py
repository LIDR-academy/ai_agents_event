"""Temporal decay: down-weight stale finalists (Session 10, Article 6).

An embedding encodes what a chunk SAYS, not when it was written or whether it is
still true — the 2019 AngularJS budget can be semantically perfect and
operationally dangerous. Temporal decay is the SOFT counterpart to a hard date
filter: instead of excluding old chunks, it multiplies their relevance by an
exponential decay so recency breaks ties without silencing history.

``weight = 0.5 ** (age_days / half_life_days)`` — a chunk exactly one half-life
old keeps half its score; the default half-life is generous (≈900 days ≈ 2.5y)
because budgets age slowly. It is applied LAST, on the finalists, AFTER reranking
("lo blando, al cierre"): the expensive cross-encoder decides relevance, then
decay only adjusts the final order. Never before reranking, or recency would
distort the candidate set the reranker sees.

Precondition: the base score must be NON-NEGATIVE (decay is a 0..1 multiplier, so
a negative base would move *up* toward zero and invert the order). The caller is
responsible for that — the advanced pipeline maps reranker logits through a
sigmoid before calling here.

Pure and synchronous — trivially unit-testable, no I/O. The reference date is
injected by the caller (so tests are deterministic and the module stays free of
wall-clock calls).
"""

from __future__ import annotations

from datetime import date

from app.generation.rag.schemas import RetrievedChunk


def decay_weight(age_days: float, half_life_days: float) -> float:
    """Exponential decay multiplier in ``(0, 1]``.

    A non-positive ``half_life_days`` disables decay (weight 1.0); a chunk dated
    in the future or with no age yields 1.0 (no penalty for being fresh).
    """
    if half_life_days <= 0 or age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def apply_temporal_decay(
    scored_chunks: list[tuple[RetrievedChunk, float]],
    *,
    half_life_days: float,
    reference_date: date,
) -> list[tuple[RetrievedChunk, float]]:
    """Re-weight ``(chunk, base_score)`` finalists by recency and re-sort.

    Each base score (assumed non-negative; see module docstring) is multiplied by
    the chunk's decay weight (from its ``document_date``; chunks without a date
    keep weight 1.0) and the list is re-ordered best-first. Returns
    ``(chunk, decayed_score)`` pairs.
    """
    if not scored_chunks:
        return []

    weighted: list[tuple[RetrievedChunk, float]] = []
    for chunk, base in scored_chunks:
        if chunk.document_date is not None:
            age_days = (reference_date - chunk.document_date).days
            weight = decay_weight(age_days, half_life_days)
        else:
            weight = 1.0
        weighted.append((chunk, base * weight))

    weighted.sort(key=lambda pair: pair[1], reverse=True)
    return weighted
