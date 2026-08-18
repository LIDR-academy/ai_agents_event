"""Unit tests for the Session 11 context augmentation passes."""

from __future__ import annotations

from app.generation.rag.quality.augmentation import (
    augment_chunks,
    extract_key_points,
    reorder_edge_loaded,
)
from app.generation.rag.schemas import RetrievedChunk


def _chunk(chunk_id: int, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=chunk_id,
        content=content,
        sector="ecommerce",
        project_year=2024,
        chunk_type="budget_component",
        distance=0.123,
    )


def test_extract_key_points_keeps_figures_and_ids_drops_filler():
    chunk = _chunk(1, "Intro prose with no data\nAUTH-001 :: OAuth backend — 120 h\nmore filler")
    key = extract_key_points(chunk)
    assert "AUTH-001 :: OAuth backend — 120 h" in key
    assert "Intro prose" not in key


def test_extract_key_points_never_empties_a_chunk():
    # A chunk with no figure/id line still yields its first non-empty line.
    assert extract_key_points(_chunk(1, "just some prose")) == "just some prose"


def test_reorder_edge_loaded_puts_strongest_at_both_ends():
    chunks = [_chunk(i, f"c{i}") for i in range(5)]  # best-first: 0,1,2,3,4
    reordered = reorder_edge_loaded(chunks)
    ids = [c.id for c in reordered]
    # Best (0) leads, second-best (1) is last, weakest sink to the middle.
    assert ids[0] == 0
    assert ids[-1] == 1
    assert ids == [0, 2, 4, 3, 1]


def test_augment_chunks_preserves_ids():
    chunks = [_chunk(i, f"ITEM-{i} :: work — {i * 10} h\nfiller") for i in range(4)]
    augmented = augment_chunks(chunks, compress=True, reorder=True)
    assert sorted(c.id for c in augmented) == [0, 1, 2, 3]
    # Compression stripped the filler line from every kept chunk.
    assert all("filler" not in c.content for c in augmented)
