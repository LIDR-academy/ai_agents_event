"""Unit tests for per-task hours estimation (Session 10).

The consensus math is a pure function and is tested directly. ``estimate_one`` /
``estimate_all`` are tested with the embedder and the retriever stubbed, so the
focus is the weighting, the reliability score and the no-match (red flag) branch.
"""

from __future__ import annotations

import pytest

import app.generation.rag.task_hours as th
from app.generation.rag.schemas import RetrievalResult, RetrievedChunk, TaskHoursModuleInput


def _chunk(cid: int, hours: int | None, distance: float) -> RetrievedChunk:
    return RetrievedChunk(
        id=cid,
        content=f"historical task ~{hours}h",
        chunk_type="historical_task",
        distance=distance,
        budget_id="TASK-2024-0001",
        estimated_hours=hours,
    )


# --- pure consensus math ---------------------------------------------------


def test_consensus_single_neighbor_uses_its_hours():
    hours, reliability, dispersion = th._consensus([(40, 0.05)])
    assert hours == 40
    assert dispersion == 0.0
    # similarity = 1 - 0.05, no dispersion penalty.
    assert reliability == pytest.approx(0.95, abs=0.01)


def test_consensus_weights_closest_neighbor_more():
    # The 30h task is far (0.4), the 50h task is very close (0.02): result skews high.
    hours, _reliability, _dispersion = th._consensus([(30, 0.4), (50, 0.02)])
    assert hours > 45


def test_consensus_high_dispersion_lowers_reliability():
    tight = th._consensus([(40, 0.1), (42, 0.1)])[1]
    spread = th._consensus([(10, 0.1), (90, 0.1)])[1]
    assert spread < tight


# --- estimate_one: match and no-match --------------------------------------


@pytest.fixture
def stub_embedder(monkeypatch):
    import app.dependencies as deps

    monkeypatch.setattr(
        deps,
        "get_embedder",
        lambda: type("E", (), {"embed_one": staticmethod(lambda t: [0.0] * 1536)})(),
    )


@pytest.fixture
def stub_runtime(monkeypatch):
    """estimate_all reads search_mode/rerank from the runtime retrieval config."""
    import app.dependencies as deps

    monkeypatch.setattr(
        deps,
        "get_runtime_retrieval_config",
        lambda: type(
            "RT",
            (),
            {
                "effective_search_mode": lambda self: "hybrid",
                "effective_rerank": lambda self: True,
                "effective_synthesis": lambda self: False,
            },
        )(),
    )


@pytest.mark.asyncio
async def test_estimate_one_match(monkeypatch, stub_embedder):
    async def fake_retrieve(**kwargs):
        # The per-task search must be filtered to the historical task corpus.
        assert kwargs["chunk_types"] == ["historical_task"]
        return RetrievalResult(
            chunks=[_chunk(1, 40, 0.1), _chunk(2, 48, 0.2)],
            low_confidence=False,
            candidates_evaluated=5,
        )

    monkeypatch.setattr(th, "retrieve", fake_retrieve)
    est = await th.estimate_one("Payments", "Gateway", "PSP", top_k=5, distance_threshold=0.45)
    assert est.has_match is True
    assert est.estimated_hours is not None
    assert 40 <= est.estimated_hours <= 48
    assert est.reliability is not None and 0.0 <= est.reliability <= 1.0
    assert len(est.neighbors) == 2
    assert est.neighbors[0].source_id == 1


@pytest.mark.asyncio
async def test_estimate_one_no_match_is_flagged(monkeypatch, stub_embedder):
    async def empty_retrieve(**kwargs):
        return RetrievalResult(chunks=[], low_confidence=True, candidates_evaluated=7)

    monkeypatch.setattr(th, "retrieve", empty_retrieve)
    est = await th.estimate_one("Quantum", "Teleporter", None, top_k=5, distance_threshold=0.45)
    assert est.has_match is False
    assert est.estimated_hours is None
    assert est.reliability is None
    assert est.neighbors == []


@pytest.mark.asyncio
async def test_estimate_all_preserves_task_order(monkeypatch, stub_embedder, stub_runtime):
    async def fake_retrieve(**kwargs):
        return RetrievalResult(
            chunks=[_chunk(1, 24, 0.15)], low_confidence=False, candidates_evaluated=3
        )

    monkeypatch.setattr(th, "retrieve", fake_retrieve)
    modules = [
        TaskHoursModuleInput(name="Auth", tasks=[{"name": "Login"}, {"name": "RBAC"}]),
        TaskHoursModuleInput(name="Payments", tasks=[{"name": "Gateway"}]),
    ]
    result = await th.estimate_all(modules, top_k=5, distance_threshold=0.45)
    assert [t.task for t in result.tasks] == ["Login", "RBAC", "Gateway"]
    assert all(t.estimated_hours == 24 for t in result.tasks)
