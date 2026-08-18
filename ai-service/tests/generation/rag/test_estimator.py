"""Flow tests for the end-to-end orchestrator (Session 9).

Every component is mocked: we validate the wiring (which stage runs, in what
order, and the soft-fail short-circuit), not the components themselves.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.dependencies as deps
from app.generation.rag import estimator as orch
from app.generation.rag.schemas import (
    Estimate,
    EstimationQuery,
    RetrievalResult,
    RetrievedChunk,
    SourceCitation,
    SourceReference,
    TaskItem,
    WorkModule,
)

_SETTINGS = SimpleNamespace(
    REFORMULATION_MODEL="gpt-5-mini",
    GENERATION_MODEL="gpt-5",
    GENERATION_REASONING_EFFORT="high",
    GENERATION_MAX_TOKENS=64_000,
    RETRIEVAL_TOP_K=10,
    RETRIEVAL_DISTANCE_THRESHOLD=0.6,
    MAX_CONTEXT_TOKENS=100_000,
    RETRIEVAL_RECALL_TOP_K=50,
    RERANK_TOP_N=5,
    RRF_K=60,
)


class CharEncoder:
    def encode(self, text: str) -> list[str]:
        return list(text)


class RecordingStore:
    def __init__(self):
        self.saved: dict[str, Estimate] = {}

    def get(self, key):
        return self.saved.get(key)

    def set(self, key, estimate):
        self.saved[key] = estimate


def _chunk(chunk_id: int) -> RetrievedChunk:
    return RetrievedChunk(
        id=chunk_id,
        content="Component: Checkout\nEstimated hours: 140",
        sector="ecommerce",
        project_year=2024,
        chunk_type="budget_component",
        distance=0.42,
    )


def _good_estimate() -> Estimate:
    return Estimate(
        total_engineer_days=18,
        duration_weeks=4,
        modules=[
            WorkModule(
                name="Checkout",
                tasks=[
                    TaskItem(
                        name="Cart & payment flow",
                        engineer_days=18,
                        grounded=True,
                        sources=[
                            SourceReference(
                                chunk_id="1",
                                document_id="BUD-2024-005",
                                evidence="Checkout component: 140 estimated hours",
                            )
                        ],
                    )
                ],
            )
        ],
        sources=[SourceCitation(source_id=1, relevance="primary", used_for="checkout")],
        assumptions=[],
        confidence="high",
        reasoning="Grounded in BUD-2024-005.",
    )


@pytest.fixture
def wire(monkeypatch):
    """Wire the orchestrator with mocked stages; return a call counter."""
    calls = {"reformulate": 0, "search": 0, "generate": 0, "embed": 0}
    store = RecordingStore()

    def _wire(*, retrieval: RetrievalResult, estimate: Estimate | None = None):
        async def fake_reformulate(transcript):
            calls["reformulate"] += 1
            return EstimationQuery(function="ecommerce storefront", sector="ecommerce")

        async def fake_retrieve(**kwargs):
            calls["search"] += 1
            return retrieval

        async def fake_generate(context_block, structured_query):
            calls["generate"] += 1
            return estimate

        def fake_embed(text):
            calls["embed"] += 1
            return [0.0] * 1536

        fake_runtime = SimpleNamespace(
            effective_search_mode=lambda: "vector",
            effective_rerank=lambda: False,
            effective_augmentation=lambda: False,
            effective_hallucination_gate=lambda: False,
        )

        monkeypatch.setattr(orch, "get_settings", lambda: _SETTINGS)
        monkeypatch.setattr(orch, "reformulate_query", fake_reformulate)
        monkeypatch.setattr(orch, "retrieve", fake_retrieve)
        monkeypatch.setattr(orch, "generate_estimate", fake_generate)
        monkeypatch.setattr(deps, "get_embedder", lambda: SimpleNamespace(embed_one=fake_embed))
        monkeypatch.setattr(deps, "get_token_encoder", lambda: CharEncoder())
        monkeypatch.setattr(deps, "get_idempotency_store", lambda: store)
        monkeypatch.setattr(deps, "get_runtime_retrieval_config", lambda: fake_runtime)
        return calls, store

    return _wire


async def test_happy_path_runs_all_stages(wire):
    retrieval = RetrievalResult(chunks=[_chunk(1)], low_confidence=False, candidates_evaluated=5)
    calls, _store = wire(retrieval=retrieval, estimate=_good_estimate())

    result = await orch.estimate_from_transcript("x" * 200)

    assert result.confidence == "high"
    assert result.total_engineer_days == 18
    assert calls == {"reformulate": 1, "search": 1, "generate": 1, "embed": 1}


async def test_soft_fail_skips_generation(wire):
    retrieval = RetrievalResult(chunks=[], low_confidence=True, candidates_evaluated=7)
    calls, _store = wire(retrieval=retrieval, estimate=_good_estimate())

    result = await orch.estimate_from_transcript("x" * 200)

    assert result.confidence == "insufficient"
    assert result.total_engineer_days is None
    assert result.insufficient_context_explanation
    assert calls["generate"] == 0  # generator never called on soft-fail


async def test_generate_estimate_passes_reasoning_token_budget(monkeypatch):
    """gpt-5 reasoning tokens count against max_tokens; _generate must pass the
    configured ceiling (and the reasoning effort) to the wrapper, or the call
    truncates with finish_reason='length'."""
    captured: dict = {}

    def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return _good_estimate(), {}

    wrapper = SimpleNamespace(complete_structured=fake_complete_structured)
    monkeypatch.setattr(orch, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: wrapper)

    estimate = await orch.generate_estimate(
        '<source id="1">x</source>', EstimationQuery(function="ecommerce storefront")
    )

    assert estimate.confidence == "high"
    assert captured["max_tokens"] == _SETTINGS.GENERATION_MAX_TOKENS
    assert captured["reasoning_effort"] == "high"
    assert captured["model_override"] == "gpt-5"


async def test_idempotency_hit_short_circuits_pipeline(wire):
    retrieval = RetrievalResult(chunks=[_chunk(1)], low_confidence=False, candidates_evaluated=5)
    calls, store = wire(retrieval=retrieval, estimate=_good_estimate())

    first = await orch.estimate_from_transcript("x" * 200, idempotency_key="k1")
    assert calls["generate"] == 1
    assert store.saved.get("k1") is not None

    second = await orch.estimate_from_transcript("x" * 200, idempotency_key="k1")
    assert second == first
    # No stage re-ran on the cached call.
    assert calls == {"reformulate": 1, "search": 1, "generate": 1, "embed": 1}
