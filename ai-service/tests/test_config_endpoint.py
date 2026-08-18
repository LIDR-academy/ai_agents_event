"""HTTP tests for GET/PUT /api/v1/config/models (runtime model settings)."""

from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import get_runtime_config
from app.foundation.llm.runtime_config import RuntimeModelConfig
from app.main import app


def make_settings(**overrides) -> Settings:
    defaults = {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"}
    return Settings(_env_file=None, **{**defaults, **overrides})


@pytest.fixture
def fake_store():
    settings = make_settings()
    store = RuntimeModelConfig(fakeredis.FakeRedis(decode_responses=True), settings)
    app.dependency_overrides[get_runtime_config] = lambda: store
    app.dependency_overrides[get_settings] = lambda: settings
    yield store
    app.dependency_overrides.clear()


@pytest.fixture
def client(fake_store) -> TestClient:
    return TestClient(app)


def test_get_returns_full_snapshot(client) -> None:
    response = client.get("/api/v1/config/models")

    assert response.status_code == 200
    body = response.json()
    assert body["models"]["PRIMARY_MODEL"] == {
        "effective": "gpt-4o-mini",
        "default": "gpt-4o-mini",
        "overridden": False,
    }
    assert set(body["models"]) == {
        "PRIMARY_MODEL",
        "FALLBACK_MODEL",
        "CRITIC_MODEL",
        "METADATA_EXTRACTOR_MODEL",
        "COMPRESSION_MODEL",
        "PROPOSITIONAL_CHUNKER_MODEL",
        "CONTEXTUAL_CHUNKER_MODEL",
        # Session 11: the hallucination judge and the augmentation compressor.
        "HALLUCINATION_JUDGE_MODEL",
        "AUGMENTATION_MODEL",
    }
    assert "gpt-4o" in body["available_models"]
    assert "claude-sonnet-4-5" in body["available_models"]
    assert body["embedding_model"] == "text-embedding-3-small"


def test_available_models_filtered_by_configured_keys(fake_store) -> None:
    # No Anthropic key → claude models leave the catalog.
    settings = make_settings(ANTHROPIC_API_KEY=None)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    body = client.get("/api/v1/config/models").json()

    assert all(not model.startswith("claude") for model in body["available_models"])
    assert "gpt-4o-mini" in body["available_models"]


def test_put_overrides_and_returns_fresh_snapshot(client, fake_store) -> None:
    response = client.put("/api/v1/config/models", json={"models": {"PRIMARY_MODEL": "gpt-4o"}})

    assert response.status_code == 200
    body = response.json()
    assert body["models"]["PRIMARY_MODEL"] == {
        "effective": "gpt-4o",
        "default": "gpt-4o-mini",
        "overridden": True,
    }
    assert fake_store.effective("PRIMARY_MODEL") == "gpt-4o"


def test_put_null_resets_override(client, fake_store) -> None:
    fake_store.set("PRIMARY_MODEL", "gpt-4o")

    response = client.put("/api/v1/config/models", json={"models": {"PRIMARY_MODEL": None}})

    assert response.status_code == 200
    assert response.json()["models"]["PRIMARY_MODEL"]["overridden"] is False
    assert fake_store.get("PRIMARY_MODEL") is None


def test_put_unknown_key_is_422(client) -> None:
    response = client.put("/api/v1/config/models", json={"models": {"EMBEDDING_MODEL": "gpt-4o"}})
    assert response.status_code == 422
    assert "Unknown model key" in response.json()["detail"]


def test_put_model_outside_catalog_is_422(client) -> None:
    response = client.put(
        "/api/v1/config/models", json={"models": {"PRIMARY_MODEL": "gpt-99-ultra"}}
    )
    assert response.status_code == 422
    assert "not in the catalog" in response.json()["detail"]


def test_put_model_with_missing_provider_key_is_400(fake_store) -> None:
    settings = make_settings(ANTHROPIC_API_KEY=None)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.put(
        "/api/v1/config/models", json={"models": {"PRIMARY_MODEL": "claude-sonnet-4-5"}}
    )

    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_put_is_all_or_nothing(client, fake_store) -> None:
    # One invalid entry → the valid one must NOT be applied either.
    response = client.put(
        "/api/v1/config/models",
        json={"models": {"PRIMARY_MODEL": "gpt-4o", "BOGUS_KEY": "gpt-4o"}},
    )

    assert response.status_code == 422
    assert fake_store.is_overridden("PRIMARY_MODEL") is False
