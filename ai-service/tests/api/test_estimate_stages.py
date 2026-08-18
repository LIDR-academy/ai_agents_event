"""Tests for the per-stage wizard endpoints (``/v1/estimate/stages/*``).

Downstream work (LLM, embeddings, pgvector) is stubbed; the focus is the auth
boundary, the stateless stage contracts, and the grounding signals from the
generate stage (which exercises the REAL ``validate_citations`` /
``check_coherence`` rather than mocking them).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routers.estimate_stages as stages
import app.api.security as security
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
from app.main import app

EST_KEY = "estimate-secret"
RET_KEY = "retrieval-secret"


def _chunk(cid: int, content: str = "Auth & RBAC component: ~12 engineer-days.") -> RetrievedChunk:
    return RetrievedChunk(
        id=cid,
        content=content,
        sector="ecommerce",
        project_year=2024,
        chunk_type="budget_component",
        distance=0.3,
    )


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: type("S", (), {"RETRIEVAL_API_KEY": RET_KEY, "ESTIMATE_API_KEY": EST_KEY})(),
    )

    async def fake_reformulate(transcript):
        return EstimationQuery(function="online store with card checkout", sector="ecommerce")

    async def fake_search(query_embedding, **kwargs):
        return RetrievalResult(
            chunks=[_chunk(1), _chunk(2)], low_confidence=False, candidates_evaluated=12
        )

    monkeypatch.setattr(stages, "reformulate_query", fake_reformulate)
    monkeypatch.setattr(
        stages, "compose_search_text", lambda q: "online store card checkout ecommerce"
    )
    monkeypatch.setattr(
        stages,
        "get_embedder",
        lambda: type("E", (), {"embed_one": staticmethod(lambda t: [0.0] * 1536)})(),
    )
    monkeypatch.setattr(stages, "search_chunks", fake_search)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _h(key=EST_KEY):
    return {"X-API-Key": key}


_TRANSCRIPT = {"transcript": "x" * 200}


# --- auth boundary ---------------------------------------------------------


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/estimate/stages/reformulate", _TRANSCRIPT),
        ("/v1/estimate/stages/retrieve", {"query_text": "online store checkout"}),
        ("/v1/estimate/stages/assemble", {"chunks": []}),
    ],
)
def test_stage_requires_estimate_key(client, path, body):
    assert client.post(path, json=body).status_code == 401
    # The retrieval key must NOT open a stage endpoint.
    assert client.post(path, json=body, headers=_h(RET_KEY)).status_code == 401


# --- reformulate -----------------------------------------------------------


def test_reformulate_returns_query_and_search_text(client):
    r = client.post("/v1/estimate/stages/reformulate", json=_TRANSCRIPT, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["query"]["sector"] == "ecommerce"
    assert body["search_text"]


# --- retrieve --------------------------------------------------------------


def test_retrieve_passes_through_chunks(client):
    r = client.post(
        "/v1/estimate/stages/retrieve",
        json={"query_text": "online store card checkout"},
        headers=_h(),
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["chunks"]) == 2
    assert body["low_confidence"] is False


def test_retrieve_soft_fail_passthrough(client, monkeypatch):
    async def empty_search(query_embedding, **kwargs):
        return RetrievalResult(chunks=[], low_confidence=True, candidates_evaluated=9)

    monkeypatch.setattr(stages, "search_chunks", empty_search)
    r = client.post(
        "/v1/estimate/stages/retrieve",
        json={"query_text": "a quantum blockchain for dog grooming"},
        headers=_h(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["chunks"] == []
    assert body["low_confidence"] is True


# --- assemble (real context_assembler + tiktoken) --------------------------


def test_assemble_wraps_chunks_in_xml(client):
    payload = {"chunks": [_chunk(1).model_dump(), _chunk(2).model_dump()]}
    r = client.post("/v1/estimate/stages/assemble", json=payload, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert '<source id="1"' in body["context_block"]
    assert body["dropped_count"] == 0
    assert body["token_count"] > 0
    assert len(body["kept_chunks"]) == 2


def test_assemble_drops_chunks_over_budget(client):
    big = [_chunk(i, content="word " * 300).model_dump() for i in range(1, 6)]
    r = client.post(
        "/v1/estimate/stages/assemble",
        json={"chunks": big, "max_context_tokens": 300},
        headers=_h(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dropped_count"] > 0
    assert len(body["kept_chunks"]) < 5


def test_assemble_augmentation_toggle(client):
    # Session 11: augment=True compresses (drops filler) and edge-load reorders.
    chunks = [
        _chunk(i, content=f"filler prose line\nITEM-{i} :: work — {i * 10} h").model_dump()
        for i in range(1, 4)
    ]
    r = client.post(
        "/v1/estimate/stages/assemble",
        json={"chunks": chunks, "augment": True},
        headers=_h(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["augmented"] is True
    assert "filler prose" not in body["context_block"]
    assert {c["id"] for c in body["kept_chunks"]} == {1, 2, 3}


# --- generate (real validate_citations + check_coherence) ------------------


def _generate_payload(estimate: Estimate) -> dict:
    return {
        "context_block": '<source id="1">...</source>',
        "query": EstimationQuery(function="online store").model_dump(),
        "kept_chunks": [_chunk(1).model_dump()],
    }


def test_generate_flags_fabricated_citations(client, monkeypatch):
    # A grounded line cites chunk_id "999", which was never retrieved (kept = [1]).
    estimate = Estimate(
        confidence="high",
        reasoning="r",
        total_engineer_days=12,
        modules=[
            WorkModule(
                name="Auth",
                tasks=[
                    TaskItem(
                        name="OAuth login",
                        engineer_days=12,
                        grounded=True,
                        sources=[
                            SourceReference(
                                chunk_id="999",
                                document_id="BUD-GHOST",
                                evidence="ghost",
                            )
                        ],
                    )
                ],
            )
        ],
        sources=[SourceCitation(source_id=1, relevance="primary", used_for="auth")],
    )

    async def fake_generate(context_block, structured_query, include_hours=True):
        return estimate

    monkeypatch.setattr(stages, "generate_estimate", fake_generate)
    r = client.post("/v1/estimate/stages/generate", json=_generate_payload(estimate), headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["fabricated_source_ids"] == ["999"]
    assert body["citation_report"]["dangling_lines"] == 1
    assert body["coherent"] is True


def test_generate_flags_incoherent_insufficient(client, monkeypatch):
    # insufficient confidence but numbers present → incoherent.
    estimate = Estimate(
        confidence="insufficient",
        reasoning="r",
        total_engineer_days=40,
        insufficient_context_explanation="should not have numbers",
    )

    async def fake_generate(context_block, structured_query, include_hours=True):
        return estimate

    monkeypatch.setattr(stages, "generate_estimate", fake_generate)
    r = client.post("/v1/estimate/stages/generate", json=_generate_payload(estimate), headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["coherent"] is False


# --- verify (Session 11: semantic hallucination gate, anchor-only) ----------


def test_verify_flags_degraded_line(client):
    # A grounded line claiming 90d against a cited 120h (≈15d) source → degraded.
    estimate = Estimate(
        confidence="high",
        reasoning="r",
        total_engineer_days=90,
        modules=[
            WorkModule(
                name="Auth",
                tasks=[
                    TaskItem(
                        name="OAuth login",
                        engineer_days=90,
                        grounded=True,
                        sources=[
                            SourceReference(chunk_id="1", document_id="BUD-1", evidence="120 h")
                        ],
                    )
                ],
            )
        ],
    )
    payload = {
        "estimate": estimate.model_dump(),
        "kept_chunks": [_chunk(1, content="AUTH :: OAuth backend — 120 h").model_dump()],
        "use_judge": False,  # anchor-only: no LLM, deterministic.
    }
    r = client.post("/v1/estimate/stages/verify", json=payload, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["total_lines"] == 1
    assert body["degraded_lines"] == 1
    assert body["lines"][0]["status"] == "degraded"


def test_verify_requires_estimate_key(client):
    body = {
        "estimate": Estimate(confidence="insufficient", reasoning="x").model_dump(),
        "kept_chunks": [],
    }
    assert client.post("/v1/estimate/stages/verify", json=body).status_code == 401
    assert (
        client.post("/v1/estimate/stages/verify", json=body, headers=_h(RET_KEY)).status_code == 401
    )


# --- structure (Session 10: free decomposition, no retrieval/sources) -------


def test_structure_returns_clean_estimate_without_sources(client, monkeypatch):
    estimate = Estimate(
        confidence="high",
        reasoning="decomposed from the brief",
        modules=[{"name": "Auth", "tasks": [{"name": "OAuth login"}, {"name": "RBAC"}]}],
    )

    async def fake_structure(query):
        return estimate

    monkeypatch.setattr(stages, "generate_structure", fake_structure)
    r = client.post(
        "/v1/estimate/stages/structure",
        json={"query": EstimationQuery(function="b2b payments portal").model_dump()},
        headers=_h(),
    )
    assert r.status_code == 200
    body = r.json()
    # Structure-only: no hours, no citations, always coherent/clean.
    assert body["estimate"]["modules"][0]["tasks"][0]["engineer_days"] is None
    assert body["fabricated_source_ids"] == []
    assert body["coherent"] is True


def test_structure_requires_estimate_key(client):
    body = {"query": {"function": "x"}}
    assert client.post("/v1/estimate/stages/structure", json=body).status_code == 401
    assert (
        client.post("/v1/estimate/stages/structure", json=body, headers=_h(RET_KEY)).status_code
        == 401
    )


# --- regression: existing endpoints still authenticate ---------------------


def test_existing_endpoints_still_work(client, monkeypatch):
    import app.api.routers.estimate as estimate_router
    import app.api.routers.retrieval as retrieval_router

    async def fake_estimate(transcript, idempotency_key=None):
        return Estimate(
            confidence="insufficient", reasoning="stub", insufficient_context_explanation="stub"
        )

    async def fake_retrieve(**kwargs):
        return RetrievalResult(chunks=[], low_confidence=True, candidates_evaluated=0)

    fake_runtime = type(
        "RT",
        (),
        {"effective_search_mode": lambda self: "vector", "effective_rerank": lambda self: False},
    )()
    monkeypatch.setattr(
        retrieval_router,
        "get_embedder",
        lambda: type("E", (), {"embed_one": staticmethod(lambda t: [0.0] * 1536)})(),
    )
    monkeypatch.setattr(retrieval_router, "get_runtime_retrieval_config", lambda: fake_runtime)
    monkeypatch.setattr(retrieval_router, "retrieve", fake_retrieve)
    monkeypatch.setattr(estimate_router, "estimate_from_transcript", fake_estimate)

    r1 = client.post(
        "/v1/retrieval/search",
        json={"query_text": "ecommerce storefront checkout"},
        headers=_h(RET_KEY),
    )
    r2 = client.post("/v1/estimate/from-transcript", json=_TRANSCRIPT, headers=_h(EST_KEY))
    assert r1.status_code == 200
    assert r2.status_code == 200
