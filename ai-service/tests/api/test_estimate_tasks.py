"""Tests for the per-task hours endpoint (``POST /v1/estimate/tasks/hours``) and
the structure-only generation flag (``/v1/estimate/stages/generate``).

The vector search is stubbed; the focus is the auth boundary, the request/response
contract (including the red-flag no-match branch) and that ``include_hours=false``
produces a structure without effort numbers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routers.estimate_stages as stages
import app.api.routers.estimate_tasks as tasks
import app.api.security as security
from app.generation.rag.schemas import (
    Estimate,
    EstimationQuery,
    TaskHoursEstimate,
    TaskHoursResult,
    TaskNeighbor,
)
from app.main import app

EST_KEY = "estimate-secret"
RET_KEY = "retrieval-secret"


@pytest.fixture(autouse=True)
def stub_keys(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: type("S", (), {"RETRIEVAL_API_KEY": RET_KEY, "ESTIMATE_API_KEY": EST_KEY})(),
    )
    monkeypatch.setattr(tasks, "get_embedder", lambda: object())
    monkeypatch.setattr(
        tasks,
        "get_runtime_retrieval_config",
        lambda: type(
            "RT",
            (),
            {
                "effective_task_hours_top_k": lambda self: 5,
                "effective_task_hours_distance_threshold": lambda self: 0.45,
            },
        )(),
    )
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _h(key=EST_KEY):
    return {"X-API-Key": key}


_BODY = {
    "modules": [
        {
            "name": "Payments",
            "tasks": [{"name": "Gateway", "description": "PSP"}, {"name": "Ghost"}],
        }
    ]
}


def test_hours_requires_estimate_key(client):
    assert client.post("/v1/estimate/tasks/hours", json=_BODY).status_code == 401
    assert (
        client.post("/v1/estimate/tasks/hours", json=_BODY, headers=_h(RET_KEY)).status_code == 401
    )


def test_hours_returns_match_and_flagged(client, monkeypatch):
    async def fake_estimate_all(modules, **kwargs):
        return TaskHoursResult(
            tasks=[
                TaskHoursEstimate(
                    module="Payments",
                    task="Gateway",
                    estimated_hours=44,
                    reliability=0.82,
                    has_match=True,
                    dispersion=0.1,
                    neighbors=[
                        TaskNeighbor(source_id=1, budget_id="b", estimated_hours=44, distance=0.1)
                    ],
                ),
                TaskHoursEstimate(module="Payments", task="Ghost", has_match=False),
            ]
        )

    monkeypatch.setattr(tasks, "estimate_all", fake_estimate_all)
    r = client.post("/v1/estimate/tasks/hours", json=_BODY, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["tasks"][0]["estimated_hours"] == 44
    assert body["tasks"][0]["has_match"] is True
    assert body["tasks"][1]["has_match"] is False
    assert body["tasks"][1]["estimated_hours"] is None


def test_hours_empty_modules_rejected(client):
    r = client.post("/v1/estimate/tasks/hours", json={"modules": []}, headers=_h())
    assert r.status_code == 422


def test_generate_structure_only_omits_hours(client, monkeypatch):
    captured = {}

    async def fake_generate(context_block, structured_query, include_hours=True):
        captured["include_hours"] = include_hours
        return Estimate(
            confidence="high",
            reasoning="structure only",
            modules=[{"name": "Auth", "tasks": [{"name": "Login"}]}],
        )

    monkeypatch.setattr(stages, "generate_estimate", fake_generate)
    payload = {
        "context_block": '<source id="1">...</source>',
        "query": EstimationQuery(function="store").model_dump(),
        "kept_chunks": [],
        "include_hours": False,
    }
    r = client.post("/v1/estimate/stages/generate", json=payload, headers=_h())
    assert r.status_code == 200
    assert captured["include_hours"] is False
    body = r.json()
    assert body["estimate"]["modules"][0]["tasks"][0]["engineer_days"] is None
