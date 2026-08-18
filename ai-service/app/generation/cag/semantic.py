"""Semantic cache for the estimator.

Two requests are considered the same when:

1. Their **bucket** matches exactly. The bucket is a deterministic tag
   composed of ``prompt_version:project_type:detail_level:output_format``.
   Two requests with different form options will NEVER share a cache entry
   even if their descriptions are similar — the rendered prompt is different,
   so the estimation should be different too.

2. The cosine similarity of their description embeddings is at least
   ``threshold`` (default 0.92).

When ``log_only=True`` the cache still does the lookup and logs the score, but
never returns a hit — useful for calibrating the threshold against real
traffic before flipping it on in production.

The store uses ``redis/redis-stack``: vanilla ``redis:7-alpine`` lacks the
RediSearch module and ``SearchIndex.create()`` will fail at startup.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

from app.domain.schemas.estimation import EstimationRequest, EstimationResult

log = structlog.get_logger()


def _to_bytes(vector: list[float]) -> bytes:
    """RediSearch stores vectors as float32 bytes. redisvl will reject lists."""
    return np.array(vector, dtype=np.float32).tobytes()


_INDEX_SCHEMA: dict[str, Any] = {
    "index": {
        "name": "estimations",
        "prefix": "estimation:semantic",
        "storage_type": "hash",
    },
    "fields": [
        {"name": "bucket", "type": "tag"},
        {"name": "result_json", "type": "text"},
        {
            "name": "embedding",
            "type": "vector",
            "attrs": {
                "dims": 1536,  # text-embedding-3-small
                "distance_metric": "cosine",
                "algorithm": "flat",
            },
        },
    ],
}


class EstimationSemanticCache:
    """Vector-similarity cache on top of redisvl + Redis Stack."""

    def __init__(
        self,
        *,
        redis_client: Any,
        vectorizer: Any,
        threshold: float = 0.92,
        ttl: int = 86400,
        log_only: bool = False,
        index_name: str = "estimations",
    ) -> None:
        from redisvl.index import SearchIndex

        self.redis_client = redis_client
        self.vectorizer = vectorizer
        self.threshold = threshold
        self.ttl = ttl
        self.log_only = log_only

        schema = dict(_INDEX_SCHEMA)
        schema["index"] = {**_INDEX_SCHEMA["index"], "name": index_name}

        self.index = SearchIndex.from_dict(schema)
        self.index.set_client(redis_client)
        try:
            self.index.create(overwrite=False)
        except Exception as exc:  # noqa: BLE001 — already-exists is fine
            log.debug("semantic_index_create_skipped", error=str(exc)[:120])

    # ------------------------------------------------------------------
    # Bucket
    # ------------------------------------------------------------------

    @staticmethod
    def bucket_for(request: EstimationRequest, prompt_version: str, model: str) -> str:
        # The model is part of the bucket (like the exact cache key): a hit
        # produced by one model must never be served while another is active —
        # critical now that the primary model can change at runtime.
        return (
            f"{prompt_version}"
            f":{request.project_type.value}"
            f":{request.detail_level.value}"
            f":{request.output_format.value}"
            f":{model}"
        )

    # ------------------------------------------------------------------
    # Lookup / store
    # ------------------------------------------------------------------

    def lookup(
        self, request: EstimationRequest, prompt_version: str, model: str
    ) -> EstimationResult | None:
        from redisvl.query import VectorQuery
        from redisvl.query.filter import Tag

        bucket = self.bucket_for(request, prompt_version, model)
        embedding = self.vectorizer.embed(request.description)

        query = VectorQuery(
            vector=_to_bytes(embedding),
            vector_field_name="embedding",
            return_fields=["result_json", "bucket"],
            num_results=1,
            return_score=True,
            filter_expression=Tag("bucket") == bucket,
        )
        results = self.index.query(query)
        if not results:
            log.info("semantic_cache_miss", bucket=bucket, reason="empty_index")
            return None

        hit = results[0]
        # redisvl returns cosine *distance* (0 = identical, up to 2 = opposite).
        distance = float(hit.get("vector_distance", 1.0))
        similarity = 1.0 - distance
        log.info(
            "semantic_cache_lookup",
            bucket=bucket,
            similarity=round(similarity, 4),
            threshold=self.threshold,
        )

        if similarity < self.threshold:
            log.info("semantic_cache_miss", bucket=bucket, reason="below_threshold")
            return None

        if self.log_only:
            log.info(
                "semantic_cache_hit_log_only",
                bucket=bucket,
                similarity=round(similarity, 4),
            )
            return None

        log.info("semantic_cache_hit", bucket=bucket, similarity=round(similarity, 4))
        return EstimationResult.model_validate_json(hit["result_json"])

    def store(
        self,
        request: EstimationRequest,
        result: EstimationResult,
        prompt_version: str,
        model: str,
    ) -> None:
        bucket = self.bucket_for(request, prompt_version, model)
        embedding = self.vectorizer.embed(request.description)
        payload = [
            {
                "bucket": bucket,
                "result_json": result.model_dump_json(),
                "embedding": _to_bytes(embedding),
            }
        ]
        try:
            self.index.load(payload, ttl=self.ttl)
            log.info("semantic_cache_stored", bucket=bucket, ttl=self.ttl)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "semantic_cache_store_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
