"""Unit tests for round-robin merge (the decomposition fusion strategy).

Pure logic, no I/O. Round-robin trades consensus for COVERAGE: every list's #1
gets a slot before any list's #2, and duplicates (by ``key``) are dropped on
first sight. That is what query decomposition needs — each sub-query owns a topic
that must be represented even if no other sub-query agrees.
"""

from __future__ import annotations

from app.generation.rag.retrieval.fusion import round_robin_merge


def test_interleaves_lists_position_by_position():
    a = [1, 2, 3]
    b = [4, 5, 6]
    assert round_robin_merge([a, b]) == [1, 4, 2, 5, 3, 6]


def test_dedupes_keeping_first_occurrence():
    a = [1, 2]
    b = [1, 3]
    # 1 appears first in list a; its later occurrence in b is dropped.
    assert round_robin_merge([a, b]) == [1, 2, 3]


def test_uneven_lists_drain_the_longer_one():
    assert round_robin_merge([[1], [2, 3, 4]]) == [1, 2, 3, 4]


def test_coverage_beats_consensus():
    # A unique topic in a short list still appears early — before the long list's
    # second item — which RRF (consensus) would not guarantee.
    popular = [10, 11, 12, 13]
    niche = [99]
    assert round_robin_merge([popular, niche])[:2] == [10, 99]


def test_composite_key_avoids_cross_collection_id_collisions():
    # Two different collections can both have id 1; the composite key keeps them.
    a = [("budget", 1), ("budget", 2)]
    b = [("transcript", 1)]
    merged = round_robin_merge([a, b], key=lambda pair: pair)
    assert ("budget", 1) in merged and ("transcript", 1) in merged
    assert len(merged) == 3


def test_empty_input():
    assert round_robin_merge([]) == []
    assert round_robin_merge([[], []]) == []
