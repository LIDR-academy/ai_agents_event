"""Tests for the CAG/RAG/hybrid architecture decision (sub-block 2.1)."""
from __future__ import annotations

from app.ingestion.architecture import (
    CAGViability,
    CorpusProfile,
    CURRENT_MODEL,
    PROYECTO_2,
    assess_cag_viability,
    recommend_architecture,
)


def test_proyecto_2_recommends_rag():
    # The headline result of the live session: the Proyecto 2 corpus does not
    # fit the usable window and needs traceability + access control → RAG.
    assert recommend_architecture(PROYECTO_2, CURRENT_MODEL) == "RAG"


def test_small_stable_unrestricted_corpus_recommends_cag():
    corpus = CorpusProfile(
        name="glosario",
        estimated_tokens=20_000,
        refresh_frequency_days=30,
        traceability_required=False,
        access_control_required=False,
    )
    assert recommend_architecture(corpus, CURRENT_MODEL) == "CAG"


def test_fits_and_cache_friendly_but_needs_traceability_recommends_hybrid():
    corpus = CorpusProfile(
        name="plantillas",
        estimated_tokens=20_000,        # cabe
        refresh_frequency_days=30,      # estable
        traceability_required=True,     # ← solo esto rompe el CAG puro
        access_control_required=False,
    )
    assert recommend_architecture(corpus, CURRENT_MODEL) == "Hybrid"


def test_cag_viability_is_an_and():
    all_green = CAGViability(True, True, True, True)
    assert all_green.viable is True
    assert all_green.failing_constraints() == []

    one_red = CAGViability(
        context_window_ok=False,
        cost_ok=True,
        latency_ok=True,
        lost_in_the_middle_ok=True,
    )
    assert one_red.viable is False
    assert one_red.failing_constraints() == ["context_window"]


def test_assess_proyecto_2_fails_the_volume_axes():
    viability = assess_cag_viability(PROYECTO_2, CURRENT_MODEL)
    assert viability.viable is False
    # 250K tokens does not fit and blows the center-recall budget.
    assert "context_window" in viability.failing_constraints()
    assert "lost_in_the_middle" in viability.failing_constraints()
