"""API-key auth tests for the Session 9 routers.

Downstream work is stubbed so a request with a valid key reaches a 200; the
focus is the 401/200 boundary and the independence of the two keys.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routers.estimate as estimate_router
import app.api.routers.retrieval as retrieval_router
import app.api.security as security
from app.generation.rag.schemas import Estimate, RetrievalResult
from app.main import app

RET_KEY = "retrieval-secret"
EST_KEY = "estimate-secret"


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: type("S", (), {"RETRIEVAL_API_KEY": RET_KEY, "ESTIMATE_API_KEY": EST_KEY})(),
    )

    async def fake_retrieve(**kwargs):
        return RetrievalResult(chunks=[], low_confidence=True, candidates_evaluated=0)

    async def fake_estimate(transcript, idempotency_key=None):
        return Estimate(
            confidence="insufficient",
            reasoning="stub",
            insufficient_context_explanation="stub",
        )

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
    yield


@pytest.fixture
def client():
    return TestClient(app)


_SEARCH_BODY = {"query_text": "ecommerce storefront with card checkout"}
_ESTIMATE_BODY = {"transcript": "x" * 200}


def test_retrieval_requires_a_key(client):
    r = client.post("/v1/retrieval/search", json=_SEARCH_BODY)
    assert r.status_code == 401


def test_retrieval_accepts_its_own_key(client):
    r = client.post("/v1/retrieval/search", json=_SEARCH_BODY, headers={"X-API-Key": RET_KEY})
    assert r.status_code == 200


def test_estimate_requires_a_key(client):
    r = client.post("/v1/estimate/from-transcript", json=_ESTIMATE_BODY)
    assert r.status_code == 401


def test_estimate_accepts_its_own_key(client):
    r = client.post(
        "/v1/estimate/from-transcript", json=_ESTIMATE_BODY, headers={"X-API-Key": EST_KEY}
    )
    assert r.status_code == 200


def test_keys_are_independent(client):
    # The retrieval key must NOT open the estimate endpoint and vice versa.
    r1 = client.post(
        "/v1/estimate/from-transcript", json=_ESTIMATE_BODY, headers={"X-API-Key": RET_KEY}
    )
    r2 = client.post("/v1/retrieval/search", json=_SEARCH_BODY, headers={"X-API-Key": EST_KEY})
    assert r1.status_code == 401
    assert r2.status_code == 401


def test_wrong_key_is_rejected(client):
    r = client.post("/v1/retrieval/search", json=_SEARCH_BODY, headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_response_carries_request_id_header(client):
    r = client.post("/v1/retrieval/search", json=_SEARCH_BODY, headers={"X-API-Key": RET_KEY})
    assert r.headers.get("X-Request-ID")
