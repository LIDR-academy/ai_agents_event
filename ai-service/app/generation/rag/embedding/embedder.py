"""OpenAI embedder.

Wraps the ``text-embedding-3-small`` model. ``embed_many`` batches chunks into
a single API call per batch (the embeddings endpoint accepts a list of inputs),
which is far cheaper in latency than one request per chunk.
"""

from __future__ import annotations

import time

import structlog
from openai import OpenAI, RateLimitError

from app.generation.rag.schemas import Chunk, EmbeddedChunk

log = structlog.get_logger()

# Default model for this exercise. We keep the full 1536-dim output (no
# Matryoshka dimension trimming — that is a live-session discussion).
MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# Number of chunks per API call. The endpoint accepts lists; batching avoids
# hammering the API with serialized single-item requests.
BATCH_SIZE = 100

# Pricing constant — CHANGES OVER TIME. As of this exercise, text-embedding-3-small
# is $0.02 per 1M input tokens. Update when OpenAI revises pricing.
PRICE_PER_MILLION_TOKENS_USD = 0.02

# Simple exponential backoff schedule (seconds) for rate-limit retries.
_RETRY_BACKOFF_SECONDS = (1, 2, 4)


def estimated_cost_usd(total_tokens: int) -> float:
    """Cost in USD for embedding ``total_tokens`` input tokens."""
    return total_tokens / 1_000_000 * PRICE_PER_MILLION_TOKENS_USD


class OpenAIEmbedder:
    """Thin wrapper over ``client.embeddings.create`` with batching + retries."""

    def __init__(self, client: OpenAI, model: str = MODEL, dimensions: int | None = None) -> None:
        self._client = client
        self._model = model
        # When set, ask OpenAI to truncate the embedding to this many dimensions
        # (Matryoshka). None = the model's native dimension (1536 for -3-small).
        self._dimensions = dimensions

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text. Used by the CLI compare script."""
        response = self._create([text])
        return response[0]

    def embed_many(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        """Embed every chunk in order, batching API calls."""
        embedded: list[EmbeddedChunk] = []
        for start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[start : start + BATCH_SIZE]
            texts = [chunk.text for chunk in batch]
            batch_tokens = sum(chunk.token_count for chunk in batch)

            t0 = time.perf_counter()
            vectors = self._create(texts)
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)

            log.info(
                "embedding_batch_done",
                chunks=len(batch),
                tokens=batch_tokens,
                latency_ms=latency_ms,
                model=self._model,
            )

            for chunk, vector in zip(batch, vectors):
                embedded.append(EmbeddedChunk(**chunk.model_dump(), embedding=vector))
        return embedded

    def _create(self, texts: list[str]) -> list[list[float]]:
        """Call the embeddings API with a simple exponential-backoff retry on
        rate limits. All other errors propagate to the caller."""
        last_error: RateLimitError | None = None
        for wait in (0, *_RETRY_BACKOFF_SECONDS):
            if wait:
                time.sleep(wait)
            try:
                kwargs = {"model": self._model, "input": texts}
                if self._dimensions is not None:
                    kwargs["dimensions"] = self._dimensions
                response = self._client.embeddings.create(**kwargs)
                return [item.embedding for item in response.data]
            except RateLimitError as exc:
                last_error = exc
                log.warning("embedding_rate_limited", retry_in_s=wait or _RETRY_BACKOFF_SECONDS[0])
        # Exhausted retries.
        raise last_error  # type: ignore[misc]
