"""Runtime-mutable model configuration, backed by Redis.

``Settings`` (.env) stays the immutable layer of *defaults*; this store holds
runtime *overrides* as deltas in a Redis hash, so the instructor can switch
models from the Settings UI mid-session without editing .env or recreating
containers. Overrides survive ``--reload`` restarts and are shared across
workers (Redis is the source of truth, read on every call).

Failure semantics:
- Reads degrade gracefully: if Redis is down, ``effective()`` falls back to
  the settings default (the service behaves exactly as configured by .env).
- Writes re-raise (`RuntimeConfigUnavailable`): a failed override must be
  visible to the caller — the API maps it to a 503.
"""

from __future__ import annotations

import redis
import structlog

from app.config import Settings

log = structlog.get_logger()

# The LLM model knobs that can be overridden at runtime. Each name is also a
# ``Settings`` field (the default). EMBEDDING_MODEL is deliberately absent:
# changing it would invalidate every stored vector, so it stays .env-only.
MODEL_KEYS: tuple[str, ...] = (
    "PRIMARY_MODEL",
    "FALLBACK_MODEL",
    "CRITIC_MODEL",
    "METADATA_EXTRACTOR_MODEL",
    "COMPRESSION_MODEL",
    "PROPOSITIONAL_CHUNKER_MODEL",
    "CONTEXTUAL_CHUNKER_MODEL",
    # Session 11: the strict hallucination judge and the augmentation compressor.
    "HALLUCINATION_JUDGE_MODEL",
    "AUGMENTATION_MODEL",
)

HASH_KEY = "estimator:runtime_config"


class RuntimeConfigUnavailable(Exception):
    """Raised when an override WRITE fails (Redis unreachable)."""


class RuntimeModelConfig:
    """Redis-hash-backed override store for the LLM model knobs."""

    def __init__(self, redis_client: redis.Redis, settings: Settings) -> None:
        self._redis = redis_client
        self._settings = settings

    @classmethod
    def from_url(cls, url: str, settings: Settings) -> "RuntimeModelConfig":
        return cls(redis.from_url(url, decode_responses=True), settings)

    def get(self, key: str) -> str | None:
        """Return the override for ``key``, or ``None`` (unset / Redis down)."""
        self._validate_key(key)
        try:
            return self._redis.hget(HASH_KEY, key)
        except redis.RedisError as exc:
            log.warning("runtime_config_read_failed", key=key, error=str(exc)[:200])
            return None

    def set(self, key: str, value: str | None) -> None:
        """Set the override for ``key``; ``None`` clears it (back to default)."""
        self._validate_key(key)
        try:
            if value is None:
                self._redis.hdel(HASH_KEY, key)
            else:
                self._redis.hset(HASH_KEY, key, value)
        except redis.RedisError as exc:
            raise RuntimeConfigUnavailable(str(exc)) from exc

    def effective(self, key: str) -> str:
        """The value the pipeline should use: override if set, else the default."""
        return self.get(key) or self.default(key)

    def default(self, key: str) -> str:
        self._validate_key(key)
        return getattr(self._settings, key)

    def is_overridden(self, key: str) -> bool:
        return self.get(key) is not None

    def snapshot(self) -> dict[str, dict[str, str | bool]]:
        """One entry per model key: ``{effective, default, overridden}``."""
        try:
            overrides = self._redis.hgetall(HASH_KEY)
        except redis.RedisError as exc:
            log.warning("runtime_config_read_failed", key="*", error=str(exc)[:200])
            overrides = {}
        return {
            key: {
                "effective": overrides.get(key) or self.default(key),
                "default": self.default(key),
                "overridden": key in overrides,
            }
            for key in MODEL_KEYS
        }

    def reset_all(self) -> None:
        """Drop every override (back to .env defaults)."""
        try:
            self._redis.delete(HASH_KEY)
        except redis.RedisError as exc:
            raise RuntimeConfigUnavailable(str(exc)) from exc

    @staticmethod
    def _validate_key(key: str) -> None:
        if key not in MODEL_KEYS:
            raise ValueError(f"Unknown model key: {key}")


# --- Session 10: runtime toggles for the retrieval pipeline ------------------
# Same Redis-hash override pattern as the model knobs, but for the two retrieval
# switches the live session flips mid-demo (search mode and reranking). Kept in a
# SEPARATE hash so resetting the model config does not clear them and vice versa.
# The settings fields are the immutable defaults: RETRIEVAL_SEARCH_MODE ("vector"
# | "hybrid") and RERANKER_ENABLED (bool).
RETRIEVAL_HASH_KEY = "estimator:runtime_retrieval"

SEARCH_MODE_KEY = "RETRIEVAL_SEARCH_MODE"
RERANK_KEY = "RERANKER_ENABLED"
# Session 10 live: the advanced-pipeline stage toggles, same override pattern so
# the instructor flips each stage from the Ajustes UI without a restart.
ROUTING_KEY = "RETRIEVAL_ROUTING_ENABLED"
QUERY_TRANSFORM_KEY = "QUERY_TRANSFORM_ENABLED"
TEMPORAL_DECAY_KEY = "TEMPORAL_DECAY_ENABLED"
# Session 10 live: per-task hours estimation knobs (numeric, not toggles). The
# distance threshold is the red-flag floor the instructor calibrates mid-session.
TASK_HOURS_TOP_K_KEY = "TASK_HOURS_TOP_K"
TASK_HOURS_DISTANCE_THRESHOLD_KEY = "TASK_HOURS_DISTANCE_THRESHOLD"
# Session 13 live: the SECOND distance cut, used by the agentic hours-recovery join.
# It is the one that decides how many tasks finally end up with no hours at all: the
# fan-out only decides who gets HANDED to the agent, the agent's own cut decides who it
# can rescue. Runtime-configurable for the same reason as the one above — it is
# calibrated against the structure of the day, at the gate-1 pause, before the fan-out.
AGENT_SEARCH_DISTANCE_THRESHOLD_KEY = "AGENT_SEARCH_DISTANCE_THRESHOLD"
# Session 11 live: generation-quality stage toggles (same override pattern), so
# the instructor flips the semantic gate, augmentation and synthesis from Ajustes.
HALLUCINATION_GATE_KEY = "HALLUCINATION_GATE_ENABLED"
AUGMENTATION_KEY = "AUGMENTATION_ENABLED"
SYNTHESIS_KEY = "SYNTHESIS_ENABLED"
RETRIEVAL_KEYS: tuple[str, ...] = (
    SEARCH_MODE_KEY,
    RERANK_KEY,
    ROUTING_KEY,
    QUERY_TRANSFORM_KEY,
    TEMPORAL_DECAY_KEY,
    TASK_HOURS_TOP_K_KEY,
    TASK_HOURS_DISTANCE_THRESHOLD_KEY,
    AGENT_SEARCH_DISTANCE_THRESHOLD_KEY,
    HALLUCINATION_GATE_KEY,
    AUGMENTATION_KEY,
    SYNTHESIS_KEY,
)

_VALID_SEARCH_MODES = ("vector", "hybrid")


class RuntimeRetrievalConfig:
    """Redis-hash-backed override store for the Session 10 retrieval toggles.

    Reads degrade to the settings default if Redis is down (the pipeline keeps
    serving exactly as .env configured it); writes re-raise so a failed override
    is visible to the API (mapped to 503), matching :class:`RuntimeModelConfig`.
    """

    def __init__(self, redis_client: redis.Redis, settings: Settings) -> None:
        self._redis = redis_client
        self._settings = settings

    @classmethod
    def from_url(cls, url: str, settings: Settings) -> "RuntimeRetrievalConfig":
        return cls(redis.from_url(url, decode_responses=True), settings)

    def _get_raw(self, key: str) -> str | None:
        try:
            return self._redis.hget(RETRIEVAL_HASH_KEY, key)
        except redis.RedisError as exc:
            log.warning("runtime_retrieval_read_failed", key=key, error=str(exc)[:200])
            return None

    def _set_raw(self, key: str, value: str | None) -> None:
        try:
            if value is None:
                self._redis.hdel(RETRIEVAL_HASH_KEY, key)
            else:
                self._redis.hset(RETRIEVAL_HASH_KEY, key, value)
        except redis.RedisError as exc:
            raise RuntimeConfigUnavailable(str(exc)) from exc

    def effective_search_mode(self) -> str:
        override = self._get_raw(SEARCH_MODE_KEY)
        if override in _VALID_SEARCH_MODES:
            return override
        return self._settings.RETRIEVAL_SEARCH_MODE

    def _effective_bool(self, key: str, default: bool) -> bool:
        override = self._get_raw(key)
        if override is None:
            return default
        return override.lower() == "true"

    def effective_rerank(self) -> bool:
        return self._effective_bool(RERANK_KEY, self._settings.RERANKER_ENABLED)

    def effective_routing(self) -> bool:
        return self._effective_bool(ROUTING_KEY, self._settings.RETRIEVAL_ROUTING_ENABLED)

    def effective_query_transform(self) -> bool:
        return self._effective_bool(QUERY_TRANSFORM_KEY, self._settings.QUERY_TRANSFORM_ENABLED)

    def effective_temporal_decay(self) -> bool:
        return self._effective_bool(TEMPORAL_DECAY_KEY, self._settings.TEMPORAL_DECAY_ENABLED)

    def effective_hallucination_gate(self) -> bool:
        return self._effective_bool(
            HALLUCINATION_GATE_KEY, self._settings.HALLUCINATION_GATE_ENABLED
        )

    def effective_augmentation(self) -> bool:
        return self._effective_bool(AUGMENTATION_KEY, self._settings.AUGMENTATION_ENABLED)

    def effective_synthesis(self) -> bool:
        return self._effective_bool(SYNTHESIS_KEY, self._settings.SYNTHESIS_ENABLED)

    def set_search_mode(self, value: str | None) -> None:
        if value is not None and value not in _VALID_SEARCH_MODES:
            raise ValueError(f"Invalid search mode: {value}")
        self._set_raw(SEARCH_MODE_KEY, value)

    def set_rerank(self, value: bool | None) -> None:
        self._set_raw(RERANK_KEY, None if value is None else str(value).lower())

    def set_bool(self, key: str, value: bool | None) -> None:
        """Set one boolean stage toggle (Session 10 routing/transform/decay or
        Session 11 gate/augmentation/synthesis)."""
        if key not in (
            ROUTING_KEY,
            QUERY_TRANSFORM_KEY,
            TEMPORAL_DECAY_KEY,
            HALLUCINATION_GATE_KEY,
            AUGMENTATION_KEY,
            SYNTHESIS_KEY,
        ):
            raise ValueError(f"Unknown retrieval toggle: {key}")
        self._set_raw(key, None if value is None else str(value).lower())

    def effective_task_hours_top_k(self) -> int:
        override = self._get_raw(TASK_HOURS_TOP_K_KEY)
        if override is None:
            return self._settings.TASK_HOURS_TOP_K
        try:
            return int(override)
        except ValueError:
            return self._settings.TASK_HOURS_TOP_K

    def effective_task_hours_distance_threshold(self) -> float:
        override = self._get_raw(TASK_HOURS_DISTANCE_THRESHOLD_KEY)
        if override is None:
            return self._settings.TASK_HOURS_DISTANCE_THRESHOLD
        try:
            return float(override)
        except ValueError:
            return self._settings.TASK_HOURS_DISTANCE_THRESHOLD

    def effective_agent_search_distance_threshold(self) -> float:
        override = self._get_raw(AGENT_SEARCH_DISTANCE_THRESHOLD_KEY)
        if override is None:
            return self._settings.AGENT_SEARCH_DISTANCE_THRESHOLD
        try:
            return float(override)
        except ValueError:
            return self._settings.AGENT_SEARCH_DISTANCE_THRESHOLD

    def set_task_hours_top_k(self, value: int | None) -> None:
        if value is not None and value < 1:
            raise ValueError("TASK_HOURS_TOP_K must be >= 1")
        self._set_raw(TASK_HOURS_TOP_K_KEY, None if value is None else str(int(value)))

    def set_task_hours_distance_threshold(self, value: float | None) -> None:
        if value is not None and not (0.0 <= value <= 2.0):
            raise ValueError("TASK_HOURS_DISTANCE_THRESHOLD must be in [0, 2]")
        self._set_raw(
            TASK_HOURS_DISTANCE_THRESHOLD_KEY, None if value is None else str(float(value))
        )

    def set_agent_search_distance_threshold(self, value: float | None) -> None:
        if value is not None and not (0.0 <= value <= 2.0):
            raise ValueError("AGENT_SEARCH_DISTANCE_THRESHOLD must be in [0, 2]")
        self._set_raw(
            AGENT_SEARCH_DISTANCE_THRESHOLD_KEY, None if value is None else str(float(value))
        )

    def snapshot(self) -> dict[str, dict[str, object]]:
        """``{effective, default, overridden}`` per retrieval toggle (for the UI)."""
        try:
            overrides = self._redis.hgetall(RETRIEVAL_HASH_KEY)
        except redis.RedisError as exc:
            log.warning("runtime_retrieval_read_failed", key="*", error=str(exc)[:200])
            overrides = {}
        return {
            SEARCH_MODE_KEY: {
                "effective": self.effective_search_mode(),
                "default": self._settings.RETRIEVAL_SEARCH_MODE,
                "overridden": SEARCH_MODE_KEY in overrides,
            },
            RERANK_KEY: {
                "effective": self.effective_rerank(),
                "default": self._settings.RERANKER_ENABLED,
                "overridden": RERANK_KEY in overrides,
            },
            ROUTING_KEY: {
                "effective": self.effective_routing(),
                "default": self._settings.RETRIEVAL_ROUTING_ENABLED,
                "overridden": ROUTING_KEY in overrides,
            },
            QUERY_TRANSFORM_KEY: {
                "effective": self.effective_query_transform(),
                "default": self._settings.QUERY_TRANSFORM_ENABLED,
                "overridden": QUERY_TRANSFORM_KEY in overrides,
            },
            TEMPORAL_DECAY_KEY: {
                "effective": self.effective_temporal_decay(),
                "default": self._settings.TEMPORAL_DECAY_ENABLED,
                "overridden": TEMPORAL_DECAY_KEY in overrides,
            },
            TASK_HOURS_TOP_K_KEY: {
                "effective": self.effective_task_hours_top_k(),
                "default": self._settings.TASK_HOURS_TOP_K,
                "overridden": TASK_HOURS_TOP_K_KEY in overrides,
            },
            AGENT_SEARCH_DISTANCE_THRESHOLD_KEY: {
                "effective": self.effective_agent_search_distance_threshold(),
                "default": self._settings.AGENT_SEARCH_DISTANCE_THRESHOLD,
                "overridden": AGENT_SEARCH_DISTANCE_THRESHOLD_KEY in overrides,
            },
            TASK_HOURS_DISTANCE_THRESHOLD_KEY: {
                "effective": self.effective_task_hours_distance_threshold(),
                "default": self._settings.TASK_HOURS_DISTANCE_THRESHOLD,
                "overridden": TASK_HOURS_DISTANCE_THRESHOLD_KEY in overrides,
            },
            HALLUCINATION_GATE_KEY: {
                "effective": self.effective_hallucination_gate(),
                "default": self._settings.HALLUCINATION_GATE_ENABLED,
                "overridden": HALLUCINATION_GATE_KEY in overrides,
            },
            AUGMENTATION_KEY: {
                "effective": self.effective_augmentation(),
                "default": self._settings.AUGMENTATION_ENABLED,
                "overridden": AUGMENTATION_KEY in overrides,
            },
            SYNTHESIS_KEY: {
                "effective": self.effective_synthesis(),
                "default": self._settings.SYNTHESIS_ENABLED,
                "overridden": SYNTHESIS_KEY in overrides,
            },
        }
