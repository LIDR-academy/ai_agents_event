#!/usr/bin/env python3
"""Embedding sanity check — cosine similarity between two texts.

Embeds two texts with ``text-embedding-3-small`` (reusing ``OpenAIEmbedder``)
and prints their cosine similarity. Cosine is computed by hand with the stdlib
``math`` module — no numpy / scikit-learn.

Usage::

    # outside the container (from the ai-service/ dir, with .env present):
    uv run python scripts/compare.py \\
        --text-a "OAuth 2.0 authentication backend for fintech" \\
        --text-b "JWT-based authorization service for banking app"

    # inside the container:
    docker compose exec ai-service python scripts/compare.py \\
        --text-a "..." --text-b "..."
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.generation.rag.embedding.embedder import OpenAIEmbedder  # noqa: E402


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot product divided by the product of the L2 norms."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cosine similarity between two embedded texts.")
    parser.add_argument("--text-a", required=True, help="First text.")
    parser.add_argument("--text-b", required=True, help="Second text.")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY is not set (check your .env).", file=sys.stderr)
        return 1

    embedder = OpenAIEmbedder(
        client=OpenAI(api_key=settings.OPENAI_API_KEY),
        model=settings.EMBEDDING_MODEL,
    )

    vec_a = embedder.embed_one(args.text_a)
    vec_b = embedder.embed_one(args.text_b)
    similarity = cosine_similarity(vec_a, vec_b)

    print(f"Text A: {args.text_a}")
    print(f"Text B: {args.text_b}")
    print(f"Cosine similarity: {similarity:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
