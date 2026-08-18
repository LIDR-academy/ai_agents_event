"""Shared helpers for the Session 8 live-session benchmark scripts.

Not a script — imported by ``measure_baseline_s08.py``, ``sweep_ef_search_s08.py``
and ``compare_indexes_s08.py`` after they bootstrap ``sys.path``. Centralizes:

* the benchmark queries (imported from ``query_examples.py`` so both files can
  never drift apart),
* query embedding via the project's ``OpenAIEmbedder`` singleton,
* latency statistics and the ``format_table`` pretty-printer (same style as
  ``compare_chunkers.py``).

Everything DB-side reuses the project's async session factory and the RAG
``ChunkStore`` — no parallel engines, no raw clients.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from query_examples import QUERIES  # noqa: E402 — single source of truth for the benchmark

from app.dependencies import get_embedder  # noqa: E402
from app.generation.rag.embedding.embedder import OpenAIEmbedder  # noqa: E402

# (label, query, embedding) triples used by every benchmark script.
EmbeddedQuery = tuple[str, str, list[float]]


def require_embedder() -> OpenAIEmbedder:
    """Return the project's embedder or exit with an actionable message."""
    embedder = get_embedder()
    if embedder is None:
        print(
            "ERROR: no OpenAI client available (OPENAI_API_KEY missing).\n"
            "Set it in ai-service/.env and recreate the container "
            "(docker compose up -d --force-recreate estimator).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return embedder


def embed_benchmark_queries(embedder: OpenAIEmbedder) -> list[EmbeddedQuery]:
    """Embed the five benchmark queries ONCE.

    The benchmark scripts time the SQL execution only: embedding latency is an
    OpenAI round-trip the index cannot influence, so it is paid upfront here
    and kept out of every measurement.
    """
    print(f"Embedding {len(QUERIES)} benchmark queries (excluded from timings)...")
    return [(label, query, embedder.embed_one(query)) for label, query in QUERIES]


def vector_literal(vector: list[float]) -> str:
    """Render a vector as the pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(component) for component in vector) + "]"


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Plain-text aligned table (same style as ``compare_chunkers.py``)."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
    return f"{line}\n{sep}\n{body}"


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


class Stopwatch:
    """Context manager that captures elapsed wall-clock milliseconds."""

    def __enter__(self) -> Stopwatch:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000


def latency_summary(samples_ms: list[float]) -> str:
    """One-line global summary: mean, median and p95 when samples allow it."""
    mean = statistics.fmean(samples_ms)
    median = statistics.median(samples_ms)
    parts = [f"mean={mean:.1f} ms", f"median={median:.1f} ms"]
    if len(samples_ms) >= 20:
        p95 = statistics.quantiles(samples_ms, n=20)[-1]
        parts.append(f"p95={p95:.1f} ms")
    return " | ".join(parts)
