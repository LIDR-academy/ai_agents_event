"""Tests for the Session 12 agent endpoints (``/v1/estimate/agent/{structure,hours}``).

Network-free: the conductor and the async OpenAI client are stubbed so the focus
is the transport boundary — auth (401), validation (422), missing client (500),
and that a successful call serializes the response tree (estimate/hours + trace).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routers.estimate_agent as agent_router
import app.api.security as security
from app.domain.schemas.agent_trace import AgentStep, AgentTrace
from app.generation.rag.schemas import (
    Estimate,
    GenerateResult,
    TaskHoursEstimate,
    TaskHoursResult,
    TaskItem,
    WorkModule,
)
from app.main import app

EST_KEY = "estimate-secret"
RET_KEY = "retrieval-secret"

_QUERY = {"query": {"function": "A SaaS billing platform", "technologies": ["Rails"]}}
_MODULES = {"modules": [{"name": "Auth", "tasks": [{"name": "OAuth backend"}]}]}


def _canned_structure() -> GenerateResult:
    return GenerateResult(
        estimate=Estimate(
            modules=[
                WorkModule(
                    name="Auth",
                    tasks=[TaskItem(name="OAuth backend", grounded=False, sources=[])],
                )
            ],
            confidence="medium",
            reasoning="Standard shape.",
        ),
        fabricated_source_ids=[],
        coherent=True,
        agent_trace=AgentTrace(
            steps=[
                AgentStep(
                    step=1,
                    reasoning_summary="Decompose into modules.",
                    tool="propose_structure",
                    tool_args={"modules": 1, "tasks": 1},
                    observation="decomposed into 1 modules / 1 tasks",
                )
            ]
        ),
    )


def _canned_hours() -> TaskHoursResult:
    return TaskHoursResult(
        tasks=[
            TaskHoursEstimate(
                module="Auth", task="OAuth backend", estimated_hours=120, reliability=0.7, has_match=True
            )
        ],
        agent_trace=AgentTrace(
            steps=[
                AgentStep(
                    step=1,
                    tool="search_budgets",
                    tool_args={"query": "oauth", "filters": None},
                    observation="2 analogs",
                )
            ]
        ),
    )


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: type("S", (), {"RETRIEVAL_API_KEY": RET_KEY, "ESTIMATE_API_KEY": EST_KEY})(),
    )
    # A truthy async client + embedder so the router does not short-circuit to 500.
    monkeypatch.setattr(agent_router, "get_async_openai_client", lambda: object())
    monkeypatch.setattr(agent_router, "get_embedder", lambda: object())

    async def fake_structure(query, *, client, model, reasoning_effort, persona=None):
        return _canned_structure()

    async def fake_hours(modules, *, client, model, reasoning_effort, max_iterations, top_k, distance_threshold, persona=None):
        return _canned_hours()

    monkeypatch.setattr(agent_router, "agent_propose_structure", fake_structure)
    monkeypatch.setattr(agent_router, "agent_estimate_task_hours", fake_hours)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _h(key=EST_KEY):
    return {"X-API-Key": key}


# --- auth ------------------------------------------------------------------ #
def test_structure_requires_estimate_key(client):
    assert client.post("/v1/estimate/agent/structure", json=_QUERY).status_code == 401
    assert (
        client.post("/v1/estimate/agent/structure", json=_QUERY, headers=_h(RET_KEY)).status_code
        == 401
    )


def test_hours_requires_estimate_key(client):
    assert client.post("/v1/estimate/agent/hours", json=_MODULES).status_code == 401


# --- validation ------------------------------------------------------------ #
def test_structure_rejects_missing_query(client):
    assert client.post("/v1/estimate/agent/structure", json={}, headers=_h()).status_code == 422


def test_hours_rejects_empty_modules(client):
    assert (
        client.post("/v1/estimate/agent/hours", json={"modules": []}, headers=_h()).status_code
        == 422
    )


def test_hours_rejects_bad_override(client):
    r = client.post(
        "/v1/estimate/agent/hours", json={**_MODULES, "search_top_k": 999}, headers=_h()
    )
    assert r.status_code == 422


# --- success --------------------------------------------------------------- #
def test_structure_returns_estimate_and_trace(client):
    r = client.post("/v1/estimate/agent/structure", json=_QUERY, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["estimate"]["modules"][0]["name"] == "Auth"
    assert body["estimate"]["modules"][0]["tasks"][0]["engineer_days"] is None
    assert body["agent_trace"]["steps"][0]["tool"] == "propose_structure"


def test_hours_returns_tasks_and_trace(client):
    r = client.post("/v1/estimate/agent/hours", json=_MODULES, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["tasks"][0]["estimated_hours"] == 120
    assert body["agent_trace"]["steps"][0]["tool"] == "search_budgets"


def test_overrides_are_passed_through(client, monkeypatch):
    captured: dict = {}

    async def capturing_hours(modules, *, client, model, reasoning_effort, max_iterations, top_k, distance_threshold, persona=None):
        captured.update(
            model=model,
            reasoning_effort=reasoning_effort,
            max_iterations=max_iterations,
            top_k=top_k,
            distance_threshold=distance_threshold,
            persona=persona,
        )
        return _canned_hours()

    monkeypatch.setattr(agent_router, "agent_estimate_task_hours", capturing_hours)
    r = client.post(
        "/v1/estimate/agent/hours",
        json={
            **_MODULES,
            "model": "gpt-5-mini",
            "reasoning_effort": "low",
            "max_iterations": 4,
            "search_top_k": 7,
            "search_distance_threshold": 0.42,
            "persona": "Be conservative.",
        },
        headers=_h(),
    )
    assert r.status_code == 200
    assert captured["model"] == "gpt-5-mini"
    assert captured["reasoning_effort"] == "low"
    assert captured["max_iterations"] == 4
    assert captured["top_k"] == 7
    assert captured["distance_threshold"] == 0.42
    assert captured["persona"] == "Be conservative."


# --- misconfiguration ------------------------------------------------------ #
def test_structure_missing_client_is_500(client, monkeypatch):
    monkeypatch.setattr(agent_router, "get_async_openai_client", lambda: None)
    r = client.post("/v1/estimate/agent/structure", json=_QUERY, headers=_h())
    assert r.status_code == 500


def test_hours_missing_embedder_is_500(client, monkeypatch):
    monkeypatch.setattr(agent_router, "get_embedder", lambda: None)
    r = client.post("/v1/estimate/agent/hours", json=_MODULES, headers=_h())
    assert r.status_code == 500
