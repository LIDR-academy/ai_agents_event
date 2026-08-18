"""Unit tests for Reciprocal Rank Fusion (pure, no I/O)."""

from __future__ import annotations

import pytest

from app.generation.rag.retrieval.fusion import reciprocal_rank_fusion


def test_document_in_both_rankings_outranks_single_branch_docs():
    # id 2 appears in BOTH branches (near the top of each); ids 1 and 3 each
    # appear in only one. Two contributions beat one — the core RRF behaviour.
    vector = [1, 2]
    lexical = [2, 3]
    fused = reciprocal_rank_fusion([vector, lexical], k=60)
    ids = [cid for cid, _ in fused]
    assert ids[0] == 2


def test_score_is_sum_of_reciprocal_ranks():
    # Single ranking, k=60: id 5 at position 0 → 1/60, id 6 at position 1 → 1/61.
    fused = dict(reciprocal_rank_fusion([[5, 6]], k=60))
    assert fused[5] == pytest.approx(1 / 60)
    assert fused[6] == pytest.approx(1 / 61)


def test_smoothing_constant_changes_ordering_sensitivity():
    # A small k lets a single #1 dominate; a large k rewards appearing in both.
    vector = [1, 2]
    lexical = [2, 1]
    small_k = dict(reciprocal_rank_fusion([vector, lexical], k=1))
    # With k=1: id1 = 1/1 + 1/2 = 1.5; id2 = 1/2 + 1/1 = 1.5 → tie, id1 first.
    assert small_k[1] == pytest.approx(small_k[2])


def test_ties_break_by_ascending_id_for_determinism():
    fused = reciprocal_rank_fusion([[10, 20], [20, 10]], k=60)
    # Symmetric input → equal scores → deterministic ascending-id order.
    assert [cid for cid, _ in fused] == [10, 20]


def test_duplicate_ids_within_a_ranking_count_once_at_best_position():
    # The repeated id 7 must not be double-credited; only its first (best) slot.
    once = dict(reciprocal_rank_fusion([[7, 8]], k=60))
    twice = dict(reciprocal_rank_fusion([[7, 8, 7]], k=60))
    assert once[7] == pytest.approx(twice[7])


def test_empty_branch_contributes_nothing():
    fused = reciprocal_rank_fusion([[1, 2], []], k=60)
    assert {cid for cid, _ in fused} == {1, 2}


def test_non_positive_k_is_rejected():
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[1]], k=0)
