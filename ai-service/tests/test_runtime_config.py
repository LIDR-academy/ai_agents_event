"""Unit tests for the Redis-backed runtime model configuration store."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
import redis as redis_lib

from app.config import Settings
from app.foundation.llm.runtime_config import (
    HASH_KEY,
    MODEL_KEYS,
    RuntimeConfigUnavailable,
    RuntimeModelConfig,
)


def make_settings(**overrides) -> Settings:
    return Settings(OPENAI_API_KEY="sk-test", _env_file=None, **overrides)


@pytest.fixture
def store() -> RuntimeModelConfig:
    return RuntimeModelConfig(fakeredis.FakeRedis(decode_responses=True), make_settings())


def test_effective_returns_settings_default_when_no_override(store) -> None:
    assert store.effective("PRIMARY_MODEL") == "gpt-4o-mini"
    assert store.is_overridden("PRIMARY_MODEL") is False


def test_set_and_effective_round_trip(store) -> None:
    store.set("PRIMARY_MODEL", "gpt-4o")
    assert store.get("PRIMARY_MODEL") == "gpt-4o"
    assert store.effective("PRIMARY_MODEL") == "gpt-4o"
    assert store.is_overridden("PRIMARY_MODEL") is True
    # Other keys untouched.
    assert store.is_overridden("CRITIC_MODEL") is False


def test_set_none_resets_to_default(store) -> None:
    store.set("PRIMARY_MODEL", "gpt-4o")
    store.set("PRIMARY_MODEL", None)
    assert store.get("PRIMARY_MODEL") is None
    assert store.effective("PRIMARY_MODEL") == "gpt-4o-mini"


def test_unknown_key_raises(store) -> None:
    with pytest.raises(ValueError, match="Unknown model key"):
        store.effective("EMBEDDING_MODEL")  # deliberately not runtime-configurable
    with pytest.raises(ValueError, match="Unknown model key"):
        store.set("NOT_A_KEY", "gpt-4o")


def test_snapshot_shape_covers_every_key(store) -> None:
    store.set("CRITIC_MODEL", "gpt-4o")
    snapshot = store.snapshot()

    assert set(snapshot) == set(MODEL_KEYS)
    assert snapshot["CRITIC_MODEL"] == {
        "effective": "gpt-4o",
        "default": "gpt-4o-mini",
        "overridden": True,
    }
    assert snapshot["PRIMARY_MODEL"]["overridden"] is False


def test_reset_all_clears_every_override(store) -> None:
    store.set("PRIMARY_MODEL", "gpt-4o")
    store.set("CRITIC_MODEL", "gpt-4o")
    store.reset_all()
    assert store.snapshot()["PRIMARY_MODEL"]["overridden"] is False
    assert store.snapshot()["CRITIC_MODEL"]["overridden"] is False


def test_reads_degrade_to_defaults_when_redis_down() -> None:
    broken = MagicMock()
    broken.hget.side_effect = redis_lib.RedisError("down")
    broken.hgetall.side_effect = redis_lib.RedisError("down")
    store = RuntimeModelConfig(broken, make_settings())

    assert store.get("PRIMARY_MODEL") is None
    assert store.effective("PRIMARY_MODEL") == "gpt-4o-mini"  # .env behaviour
    assert store.snapshot()["PRIMARY_MODEL"]["overridden"] is False


def test_writes_raise_when_redis_down() -> None:
    broken = MagicMock()
    broken.hset.side_effect = redis_lib.RedisError("down")
    store = RuntimeModelConfig(broken, make_settings())

    with pytest.raises(RuntimeConfigUnavailable):
        store.set("PRIMARY_MODEL", "gpt-4o")


def test_overrides_share_the_hash_key(store) -> None:
    store.set("PRIMARY_MODEL", "gpt-4o")
    assert store._redis.hgetall(HASH_KEY) == {"PRIMARY_MODEL": "gpt-4o"}
