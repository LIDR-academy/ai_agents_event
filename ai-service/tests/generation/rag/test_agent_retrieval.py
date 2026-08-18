"""Tests for the agent's retrieval backend (moved out of agentic into rag).

No network, no DB: the embedder and ``retrieve()`` are stubbed. The backend takes
plain ``(query, sectors)`` — structural, so agentic never imports rag.
"""

from __future__ import annotations

import app.dependencies as dependencies
import app.generation.rag.agent_retrieval as agent_retrieval
from app.generation.rag.agent_retrieval import make_retrieval_backend


class _FakeChunk:
    def __init__(self):
        self.id = 1
        self.content = "logistics backend"
        self.sector = "logistics"
        self.budget_id = "B-1"
        self.estimated_hours = 940.0
        self.distance = 0.12


def _stub_embedder(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "get_embedder",
        lambda: type("E", (), {"embed_one": staticmethod(lambda t: [0.0] * 1536)})(),
    )


async def test_backend_passes_overrides_and_sectors_to_retrieve(monkeypatch):
    captured: dict = {}

    class _Result:
        chunks = [_FakeChunk()]

    async def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return _Result()

    _stub_embedder(monkeypatch)
    monkeypatch.setattr(agent_retrieval, "retrieve", fake_retrieve)

    backend = make_retrieval_backend(top_k=3, distance_threshold=0.42)
    items = await backend("logistics backend", ["logistics"])

    assert captured["top_k"] == 3
    assert captured["distance_threshold"] == 0.42
    assert captured["sectors"] == ["logistics"]
    assert captured["chunk_types"] == ["historical_task"]
    assert items[0]["estimated_hours"] == 940.0
    assert items[0]["distance"] == 0.12


async def test_backend_falls_back_to_settings(monkeypatch):
    captured: dict = {}

    class _Result:
        chunks = []

    async def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return _Result()

    _stub_embedder(monkeypatch)
    monkeypatch.setattr(agent_retrieval, "retrieve", fake_retrieve)

    backend = make_retrieval_backend()  # the default: no overrides
    await backend("x", None)

    settings = agent_retrieval.get_settings()
    assert captured["top_k"] == settings.AGENT_SEARCH_TOP_K
    assert captured["distance_threshold"] == settings.AGENT_SEARCH_DISTANCE_THRESHOLD
    assert captured["sectors"] is None
