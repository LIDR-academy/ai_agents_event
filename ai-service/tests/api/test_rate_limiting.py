"""Rate-limiting tests for the Session 9 estimate endpoint (10/minute).

A unique API key per test isolates the slowapi bucket (the limiter key is the
``X-API-Key`` header) from the other API tests sharing the process.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routers.estimate as estimate_router
import app.api.security as security
from app.generation.rag.schemas import Estimate
from app.main import app

_LIMIT_KEY = "ratelimit-estimate-key"
_BODY = {"transcript": "x" * 200}


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: type("S", (), {"RETRIEVAL_API_KEY": "r", "ESTIMATE_API_KEY": _LIMIT_KEY})(),
    )

    async def fake_estimate(transcript, idempotency_key=None):
        return Estimate(
            confidence="insufficient", reasoning="stub", insufficient_context_explanation="stub"
        )

    monkeypatch.setattr(estimate_router, "estimate_from_transcript", fake_estimate)
    yield


def test_estimate_returns_429_with_retry_after_when_limit_exceeded():
    client = TestClient(app)
    headers = {"X-API-Key": _LIMIT_KEY}

    statuses = [
        client.post("/v1/estimate/from-transcript", json=_BODY, headers=headers).status_code
        for _ in range(11)  # limit is 10/minute
    ]

    assert statuses[:10] == [200] * 10
    blocked = client.post("/v1/estimate/from-transcript", json=_BODY, headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") == "60"
    body = blocked.json()
    assert body["retry_after_seconds"] == 60
