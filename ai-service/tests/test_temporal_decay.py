"""Unit tests for temporal decay (the soft recency re-weighting).

Pure math, no I/O: the half-life formula, the future/no-date guards, the
min-max normalisation that makes multiplicative decay safe over (possibly
negative) reranker scores, and the re-sorting it produces.
"""

from __future__ import annotations

from datetime import date

from app.generation.rag.retrieval.temporal import apply_temporal_decay, decay_weight
from app.generation.rag.schemas import RetrievedChunk


def _chunk(chunk_id: int, document_date: date | None) -> RetrievedChunk:
    return RetrievedChunk(
        id=chunk_id,
        content=f"chunk-{chunk_id}",
        chunk_type="budget_component",
        distance=0.2,
        document_date=document_date,
    )


def test_decay_weight_halves_after_one_half_life():
    assert decay_weight(900, 900) == 0.5
    assert decay_weight(1800, 900) == 0.25


def test_decay_weight_guards():
    assert decay_weight(0, 900) == 1.0  # fresh
    assert decay_weight(-10, 900) == 1.0  # future date
    assert decay_weight(900, 0) == 1.0  # decay disabled (non-positive half-life)


def test_recent_chunk_overtakes_slightly_more_relevant_stale_one():
    today = date(2024, 1, 1)
    old_but_relevant = _chunk(1, date(2018, 1, 1))  # ~6y old
    recent = _chunk(2, date(2023, 12, 1))  # ~1 month old
    # Base scores: the old one is marginally more relevant.
    scored = [(old_but_relevant, 1.0), (recent, 0.9)]

    out = apply_temporal_decay(scored, half_life_days=900, reference_date=today)

    assert [chunk.id for chunk, _ in out] == [2, 1]  # recency flips the order


def test_chunks_without_date_keep_full_weight():
    today = date(2024, 1, 1)
    dated_old = _chunk(1, date(2016, 1, 1))
    undated = _chunk(2, None)
    scored = [(dated_old, 0.8), (undated, 0.8)]

    out = apply_temporal_decay(scored, half_life_days=900, reference_date=today)

    # Equal base scores → normalised to 1.0 each; the undated one is not penalised
    # while the old dated one is, so the undated chunk wins.
    assert out[0][0].id == 2


def test_empty_input():
    assert apply_temporal_decay([], half_life_days=900, reference_date=date(2024, 1, 1)) == []
