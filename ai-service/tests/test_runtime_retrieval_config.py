"""The Redis-backed retrieval knobs, unit + HTTP.

Focused on the two DISTANCE CUTS, because they are easy to confuse and they do
different jobs:

* ``TASK_HOURS_DISTANCE_THRESHOLD`` — the deterministic fan-out cut. Decides which
  tasks come back without hours and are therefore HANDED to the recovery agent.
* ``AGENT_SEARCH_DISTANCE_THRESHOLD`` — the recovery agent's own cut. Decides which of
  those it can actually rescue, and hence how many tasks finish with no hours at all.

With a large corpus a loose value on the second one means nothing is ever left
unmatched, regardless of the first — which is why it has to be tunable at runtime
rather than frozen in ``.env`` at process start.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import get_runtime_retrieval_config
from app.foundation.llm.runtime_config import (
    AGENT_SEARCH_DISTANCE_THRESHOLD_KEY,
    RETRIEVAL_KEYS,
    RuntimeRetrievalConfig,
)
from app.main import app


def make_settings(**overrides) -> Settings:
    return Settings(OPENAI_API_KEY="sk-test", _env_file=None, **overrides)


@pytest.fixture
def store() -> RuntimeRetrievalConfig:
    return RuntimeRetrievalConfig(fakeredis.FakeRedis(decode_responses=True), make_settings())


@pytest.fixture
def client(store) -> TestClient:
    settings = make_settings()
    app.dependency_overrides[get_runtime_retrieval_config] = lambda: store
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- unit ------------------------------------------------------------------


def test_agent_search_threshold_defaults_to_the_setting(store) -> None:
    assert store.effective_agent_search_distance_threshold() == 0.6


def test_agent_search_threshold_round_trips(store) -> None:
    store.set_agent_search_distance_threshold(0.45)
    assert store.effective_agent_search_distance_threshold() == 0.45


def test_agent_search_threshold_none_resets_to_default(store) -> None:
    store.set_agent_search_distance_threshold(0.45)
    store.set_agent_search_distance_threshold(None)
    assert store.effective_agent_search_distance_threshold() == 0.6


def test_agent_search_threshold_rejects_out_of_range(store) -> None:
    with pytest.raises(ValueError):
        store.set_agent_search_distance_threshold(2.5)


def test_the_two_distance_cuts_are_independent(store) -> None:
    """Overriding the fan-out cut must not move the recovery cut, or vice versa."""
    store.set_task_hours_distance_threshold(0.44)
    assert store.effective_agent_search_distance_threshold() == 0.6

    store.set_agent_search_distance_threshold(0.45)
    assert store.effective_task_hours_distance_threshold() == 0.44


def test_reads_degrade_to_the_setting_when_redis_is_down() -> None:
    broken = MagicMock()
    broken.hget.side_effect = redis_lib.RedisError("down")
    broken.hgetall.side_effect = redis_lib.RedisError("down")
    store = RuntimeRetrievalConfig(broken, make_settings())

    assert store.effective_agent_search_distance_threshold() == 0.6
    snap = store.snapshot()[AGENT_SEARCH_DISTANCE_THRESHOLD_KEY]
    assert snap["overridden"] is False


def test_snapshot_covers_every_retrieval_key(store) -> None:
    assert set(store.snapshot()) == set(RETRIEVAL_KEYS)


# --- HTTP ------------------------------------------------------------------


def test_put_sets_the_recovery_cut_and_reports_it(client, store) -> None:
    body = client.put(
        "/api/v1/config/retrieval", json={"agent_search_distance_threshold": 0.45}
    ).json()
    entry = body["retrieval"][AGENT_SEARCH_DISTANCE_THRESHOLD_KEY]
    assert entry == {"effective": 0.45, "default": 0.6, "overridden": True}
    assert store.effective_agent_search_distance_threshold() == 0.45


def test_put_null_resets_the_recovery_cut(client, store) -> None:
    client.put("/api/v1/config/retrieval", json={"agent_search_distance_threshold": 0.45})
    body = client.put(
        "/api/v1/config/retrieval", json={"agent_search_distance_threshold": None}
    ).json()
    assert body["retrieval"][AGENT_SEARCH_DISTANCE_THRESHOLD_KEY]["overridden"] is False


def test_put_omitting_the_field_leaves_it_untouched(client, store) -> None:
    """Absent field ≠ explicit null — the endpoint already distinguishes them."""
    store.set_agent_search_distance_threshold(0.45)
    client.put("/api/v1/config/retrieval", json={"task_hours_top_k": 5})
    assert store.effective_agent_search_distance_threshold() == 0.45


def test_put_out_of_range_is_422(client) -> None:
    assert (
        client.put(
            "/api/v1/config/retrieval", json={"agent_search_distance_threshold": 2.5}
        ).status_code
        == 422
    )
