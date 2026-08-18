"""Integration test: multi-turn session accumulates ProjectMetadata.

We stub ``get_llm_wrapper`` with a fake whose ``complete_structured_chat``
returns canned ``EstimationResult`` / ``ProjectMetadata`` instances so the
test runs offline and deterministically.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    get_estimation_service,
    get_llm_wrapper,
    get_openai_client,
    get_session_store,
)
from app.main import app
from app.domain.schemas.estimation import EstimationResult
from app.domain.estimation_service import EstimationService
from app.generation.conversation.models import ProjectMetadata
from app.generation.conversation.store import SessionStore


def _canned_result() -> EstimationResult:
    return EstimationResult(
        summary="Mid-sized B2B CRM build for the sales team.",
        confidence_pct=72,
        phases=[
            {"name": "Discovery", "duration_weeks": 1, "cost_eur": 5_000,
             "summary": "Workshops + tech spike for the CRM scope."},
            {"name": "Build", "duration_weeks": 5, "cost_eur": 20_000,
             "summary": "Core CRM features with React + Postgres."},
        ],
        total_duration_weeks=6,
        total_cost_eur=25_000,
    )


class _FakeLLMWrapper:
    """Captures calls and returns turn-specific canned outputs."""

    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        # Each tuple: (EstimationResult to return for the estimation call,
        # ProjectMetadata to return for the metadata extractor call).
        self.scripted: list[tuple[EstimationResult, ProjectMetadata]] = []
        self._turn = 0

    def _next(self) -> tuple[EstimationResult, ProjectMetadata]:
        idx = self._turn // 2  # two chat calls per turn (estimation + extractor)
        return self.scripted[idx]

    def complete_structured_chat(self, *, messages, response_model, **kwargs):
        self.chat_calls.append(
            {"messages": messages, "response_model": response_model.__name__, "kwargs": kwargs}
        )
        result, metadata = self._next()
        self._turn += 1
        meta = {"model": "gpt-4o-mini", "provider": "openai", "latency_ms": 1}
        if response_model is EstimationResult:
            return result, meta
        # metadata extractor path
        return metadata, meta


@pytest.fixture
def fake_wrapper() -> _FakeLLMWrapper:
    return _FakeLLMWrapper()


@pytest.fixture
def isolated_store() -> SessionStore:
    return SessionStore(max_turns=6)


@pytest.fixture
def client(fake_wrapper: _FakeLLMWrapper, isolated_store: SessionStore) -> TestClient:
    # Build an EstimationService using the fake wrapper, no caches.
    service = EstimationService(
        llm_wrapper=fake_wrapper,
        exact_cache=None,
        semantic_cache=None,
        openai_client=None,
        metadata_extractor_model="gpt-4o-mini",
    )
    app.dependency_overrides[get_estimation_service] = lambda: service
    app.dependency_overrides[get_session_store] = lambda: isolated_store
    app.dependency_overrides[get_llm_wrapper] = lambda: fake_wrapper
    # The conversational pipeline does not call OpenAI moderation when the
    # client is None, so this keeps the input guardrail offline.
    app.dependency_overrides[get_openai_client] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


VALID_FORM = {
    "transcript": "We want a CRM called Nimbus built with React and Postgres for the sales team.",
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table",
}


def test_two_turns_accumulate_metadata(
    client: TestClient, fake_wrapper: _FakeLLMWrapper
) -> None:
    fake_wrapper.scripted = [
        (
            _canned_result(),
            ProjectMetadata(
                project_name="Nimbus",
                assumed_team_size=3,
                mentioned_technologies=["React", "Postgres"],
                agreed_scope="Phase 1 MVP CRM for sales team.",
            ),
        ),
        (
            _canned_result(),
            ProjectMetadata(
                project_name=None,
                assumed_team_size=None,
                mentioned_technologies=["Stripe"],
                agreed_scope="Phase 1 MVP CRM with billing.",
            ),
        ),
    ]

    create = client.post("/sessions")
    assert create.status_code == 201
    session_id = create.json()["session_id"]

    # Turn 1
    r1 = client.post(f"/sessions/{session_id}/estimate", data=VALID_FORM)
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["prompt_version"] == "v3"
    assert body["cached"] is False
    assert body["result"]["total_cost_eur"] == 25_000

    # Turn 2 — adds billing
    follow_up = {**VALID_FORM, "transcript": "Now add Stripe-based billing on top."}
    r2 = client.post(f"/sessions/{session_id}/estimate", data=follow_up)
    assert r2.status_code == 200, r2.text

    # The session should reflect the merged metadata.
    info = client.get(f"/sessions/{session_id}").json()
    assert info["metadata"]["project_name"] == "Nimbus"
    assert info["metadata"]["assumed_team_size"] == 3
    assert sorted(info["metadata"]["mentioned_technologies"]) == sorted(
        ["React", "Postgres", "Stripe"]
    )
    assert "billing" in info["metadata"]["agreed_scope"].lower()


def test_session_404_returns_not_found(client: TestClient) -> None:
    r = client.post("/sessions/does-not-exist/estimate", data=VALID_FORM)
    assert r.status_code == 404
    assert r.json()["detail"] == "session_not_found"


def test_create_session_returns_unique_ids(client: TestClient) -> None:
    a = client.post("/sessions").json()["session_id"]
    b = client.post("/sessions").json()["session_id"]
    assert a != b
