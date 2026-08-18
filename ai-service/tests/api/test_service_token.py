"""Session 15 — the service-to-service token guard.

These tests deliberately turn the middleware ON (the suite-wide autouse fixture
in ``conftest.py`` keeps it off everywhere else), because what matters here is
exactly the behaviour that fixture suppresses.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.api.service_token import SERVICE_TOKEN_HEADER
from app.main import app

TOKEN = "s15-secret-token"


@pytest.fixture
def secured_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client against an app that requires the service token."""
    monkeypatch.setattr(get_settings(), "AI_SERVICE_TOKEN", TOKEN)
    return TestClient(app)


def test_health_stays_open_so_the_healthcheck_works(secured_client: TestClient) -> None:
    """/health must never require the token.

    The Docker HEALTHCHECK and compose's ``condition: service_healthy`` both
    probe it and neither can carry a secret. If this regresses, the AI service
    never becomes healthy and business-backend never starts.
    """
    response = secured_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_docs_stay_open(secured_client: TestClient) -> None:
    assert secured_client.get("/openapi.json").status_code == 200


def test_request_without_token_is_rejected(secured_client: TestClient) -> None:
    response = secured_client.post("/api/v1/estimate", json={})

    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "invalid_service_token"
    assert response.headers["WWW-Authenticate"] == SERVICE_TOKEN_HEADER


def test_request_with_wrong_token_is_rejected(secured_client: TestClient) -> None:
    response = secured_client.post(
        "/api/v1/estimate", json={}, headers={SERVICE_TOKEN_HEADER: "wrong"}
    )

    assert response.status_code == 401


def test_the_guard_covers_endpoints_that_had_no_auth_before(
    secured_client: TestClient,
) -> None:
    """The pre-Session-9 surface was completely unauthenticated.

    ``PUT /api/v1/config/models`` is the pointed one: it mutates runtime model
    configuration, and until this middleware existed anyone who could reach the
    port could call it.
    """
    assert secured_client.get("/api/v1/config/models").status_code == 401

    for method, path in (
        ("put", "/api/v1/config/models"),
        ("post", "/search"),
        ("post", "/sessions"),
    ):
        response = getattr(secured_client, method)(path, json={})
        assert response.status_code == 401, f"{method.upper()} {path} was not guarded"


def test_a_valid_token_passes_through(secured_client: TestClient) -> None:
    """With the right token the request reaches the router.

    The assertion is "not 401": the payload is empty, so the endpoint itself
    answers 422. That is the point — the guard let it through and validation,
    not authentication, is what rejected it.
    """
    response = secured_client.post(
        "/api/v1/estimate", json={}, headers={SERVICE_TOKEN_HEADER: TOKEN}
    )

    assert response.status_code != 401
    assert response.status_code == 422


def test_the_two_auth_layers_are_independent(secured_client: TestClient) -> None:
    """A valid service token does NOT grant access to a keyed router.

    The service token answers "may you talk to me at all"; the Session 9
    ``X-API-Key`` answers "which endpoints may you use". Collapsing the two
    would silently widen the blast radius of the shared secret.
    """
    response = secured_client.post(
        "/v1/retrieval/search",
        json={"query": "crm"},
        headers={SERVICE_TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "X-API-Key"


def test_unset_token_disables_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank token ⇒ middleware off.

    This is the opposite of the Session 9 keys (unset ⇒ 401 on everything) and
    it is intentional: the guard wraps the entire app, so defaulting it on would
    break every local run and every test the day it shipped.
    """
    monkeypatch.setattr(get_settings(), "AI_SERVICE_TOKEN", None)
    client = TestClient(app)

    assert client.post("/api/v1/estimate", json={}).status_code == 422
