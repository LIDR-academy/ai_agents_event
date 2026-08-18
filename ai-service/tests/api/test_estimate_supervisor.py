"""Transport tests for ``/v1/estimate/supervisor`` (Session 14).

Network-free, but driven by a REAL compiled graph over a ``MemorySaver`` with the LLM
and retrieval doubled. Testing against genuine snapshots rather than a stubbed graph is
what makes the interesting assertions trustworthy: that a paused run really reports
``awaiting_human_review``, and that resuming one that is not paused really 409s.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

import app.api.security as security
from app.domain.graph.supervisor.build import build_supervisor_graph
from app.main import app
from tests.domain.graph.supervisor.conftest import FULL_ROUTE, FakeWrapper, wire

EST_KEY = "estimate-secret"
RET_KEY = "retrieval-secret"

TRANSCRIPT = "A" * 200
GROUNDED = {"API": [80, 96, 88], "App": [120, 140, 130]}

_UNGROUNDED_ESTIMATE = {
    "components": [{"name": "API", "engineer_days": 10, "rationale": "guessed"}],
    "total_engineer_days": 10,
    "confidence": "low",
    "reasoning": "no analogs found",
}


@pytest.fixture(autouse=True)
def auth(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: type("S", (), {"RETRIEVAL_API_KEY": RET_KEY, "ESTIMATE_API_KEY": EST_KEY})(),
    )
    yield


def _install_graph(monkeypatch, *, hours_by_component, estimate=None):
    wrapper = FakeWrapper(route_script=list(FULL_ROUTE), estimate=estimate)
    wire(monkeypatch, wrapper=wrapper, hours_by_component=hours_by_component)
    app.state.supervisor_graph = build_supervisor_graph(MemorySaver())


@pytest.fixture
def client():
    return TestClient(app)


def _h(key=EST_KEY):
    return {"X-API-Key": key}


def _body(estimation_id: str) -> dict:
    return {"transcript": TRANSCRIPT, "estimation_id": estimation_id}


# --- auth + validation ------------------------------------------------------ #
def test_start_requires_the_estimate_key(client):
    assert client.post("/v1/estimate/supervisor", json=_body("x")).status_code == 401
    assert (
        client.post("/v1/estimate/supervisor", json=_body("x"), headers=_h(RET_KEY)).status_code
        == 401
    )


def test_a_short_transcript_is_rejected(client):
    response = client.post(
        "/v1/estimate/supervisor", json={"transcript": "too short"}, headers=_h()
    )
    assert response.status_code == 422


def test_an_unknown_decision_is_rejected(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component=GROUNDED)
    response = client.post(
        "/v1/estimate/supervisor/whatever/resume",
        json={"decision": "maybe"},
        headers=_h(),
    )
    assert response.status_code == 422


def test_503_when_the_graph_failed_to_build(client, monkeypatch):
    monkeypatch.setattr(app.state, "supervisor_graph", None, raising=False)
    response = client.post("/v1/estimate/supervisor", json=_body("x"), headers=_h())
    assert response.status_code == 503


# --- the happy path --------------------------------------------------------- #
def test_a_grounded_run_completes_with_its_status(client, monkeypatch):
    """The contract that never changes: transcript in, estimate + status out."""
    _install_graph(monkeypatch, hours_by_component=GROUNDED)
    response = client.post("/v1/estimate/supervisor", json=_body("run-ok"), headers=_h())

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "completed"
    assert body["status"] == "validated"
    assert body["pending_review"] is None
    assert body["estimate"]["total_engineer_days"] == 20
    # The routing is surfaced so the supervisor's decisions are inspectable.
    assert [row["next_agent"] for row in body["routing_history"]] == FULL_ROUTE
    assert body["privilege_violations"] == []


def test_an_estimation_id_is_minted_when_omitted(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component=GROUNDED)
    response = client.post("/v1/estimate/supervisor", json={"transcript": TRANSCRIPT}, headers=_h())
    assert response.status_code == 200
    assert response.json()["estimation_id"]


# --- the human-in-the-loop path --------------------------------------------- #
def test_an_ungrounded_run_pauses_for_review(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component={}, estimate=_UNGROUNDED_ESTIMATE)
    response = client.post("/v1/estimate/supervisor", json=_body("run-pause"), headers=_h())

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "paused"
    assert body["status"] == "awaiting_human_review"
    review = body["pending_review"]
    assert review["gate"] == "low_confidence_review"
    assert review["estimation_id"] == "run-pause"
    assert len(review["reasons"]) == 2  # low confidence + no precedent
    assert review["threshold"] == 0.6


def test_state_lets_a_ui_recover_a_paused_run(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component={}, estimate=_UNGROUNDED_ESTIMATE)
    client.post("/v1/estimate/supervisor", json=_body("run-state"), headers=_h())

    response = client.get("/v1/estimate/supervisor/run-state/state", headers=_h())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_human_review"
    assert body["pending_review"]["reasons"]


def test_unknown_estimation_id_is_404(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component=GROUNDED)
    assert client.get("/v1/estimate/supervisor/nope/state", headers=_h()).status_code == 404


def test_resume_continues_the_run_to_completion(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component={}, estimate=_UNGROUNDED_ESTIMATE)
    client.post("/v1/estimate/supervisor", json=_body("run-resume"), headers=_h())

    response = client.post(
        "/v1/estimate/supervisor/run-resume/resume",
        json={"decision": "approve", "note": "checked with the client"},
        headers=_h(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "completed"
    assert body["status"] == "validated"
    assert body["pending_review"] is None
    assert body["human_decision"]["note"] == "checked with the client"


def test_resume_with_reject_marks_the_run_rejected(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component={}, estimate=_UNGROUNDED_ESTIMATE)
    client.post("/v1/estimate/supervisor", json=_body("run-reject"), headers=_h())

    response = client.post(
        "/v1/estimate/supervisor/run-reject/resume",
        json={"decision": "reject", "note": "not viable"},
        headers=_h(),
    )
    assert response.json()["status"] == "rejected"


def test_resuming_a_finished_run_is_409(client, monkeypatch):
    """Idempotency guard: only a run that is actually paused can be resumed."""
    _install_graph(monkeypatch, hours_by_component={}, estimate=_UNGROUNDED_ESTIMATE)
    client.post("/v1/estimate/supervisor", json=_body("run-409"), headers=_h())
    client.post(
        "/v1/estimate/supervisor/run-409/resume", json={"decision": "approve"}, headers=_h()
    )

    second = client.post(
        "/v1/estimate/supervisor/run-409/resume", json={"decision": "approve"}, headers=_h()
    )
    assert second.status_code == 409


def test_resuming_an_unknown_run_is_409(client, monkeypatch):
    _install_graph(monkeypatch, hours_by_component=GROUNDED)
    response = client.post(
        "/v1/estimate/supervisor/never-started/resume",
        json={"decision": "approve"},
        headers=_h(),
    )
    assert response.status_code == 409
