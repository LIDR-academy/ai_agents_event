"""Idempotency cache for the estimate endpoint (Session 9).

A client may safely retry ``POST /v1/estimate/from-transcript`` with the same
``idempotency_key`` and get the original result back without paying for the LLM
pipeline again. Two interchangeable backends behind one contract
(``get`` / ``set`` / ``delete``):

* **Redis** when ``REDIS_URL`` is configured — survives restarts, shared across
  workers, native TTL.
* **In-process dict** otherwise — a thread-safe fallback with manual expiry, so
  the feature works in tests and single-process dev without Redis.
"""

from __future__ import annotations

import threading
import time

import structlog

from app.generation.rag.schemas import Estimate

log = structlog.get_logger()

_KEY_PREFIX = "idempotency:estimate:"


class IdempotencyStore:
    """Store grounded :class:`Estimate` results keyed by client idempotency key."""

    def __init__(self, redis_client=None, ttl: int = 86400) -> None:
        self._redis = redis_client
        self._ttl = ttl
        self._mem: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings) -> "IdempotencyStore":
        """Build the store, preferring Redis and degrading to the dict fallback."""
        redis_client = None
        if settings.REDIS_URL:
            try:
                import redis

                redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
                redis_client.ping()
            except Exception as exc:  # noqa: BLE001 — fall back to in-memory.
                log.warning(
                    "idempotency_redis_unavailable",
                    reason="setup_failed",
                    error_type=type(exc).__name__,
                )
                redis_client = None
        return cls(redis_client=redis_client, ttl=settings.IDEMPOTENCY_TTL)

    def get(self, key: str) -> Estimate | None:
        """Return the cached estimate for ``key``, or ``None`` on a miss."""
        raw = self._get_raw(key)
        if raw is None:
            return None
        try:
            return Estimate.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — a corrupt entry is a miss.
            self.delete(key)
            return None

    def set(self, key: str, estimate: Estimate) -> None:
        """Cache ``estimate`` under ``key`` with the configured TTL."""
        raw = estimate.model_dump_json()
        if self._redis is not None:
            self._redis.set(_KEY_PREFIX + key, raw, ex=self._ttl)
            return
        with self._lock:
            self._mem[key] = (raw, time.monotonic() + self._ttl)

    def delete(self, key: str) -> None:
        """Remove ``key`` from the store (no-op if absent)."""
        if self._redis is not None:
            self._redis.delete(_KEY_PREFIX + key)
            return
        with self._lock:
            self._mem.pop(key, None)

    def _get_raw(self, key: str) -> str | None:
        if self._redis is not None:
            return self._redis.get(_KEY_PREFIX + key)
        with self._lock:
            entry = self._mem.get(key)
            if entry is None:
                return None
            raw, expires_at = entry
            if time.monotonic() >= expires_at:
                self._mem.pop(key, None)
                return None
            return raw
