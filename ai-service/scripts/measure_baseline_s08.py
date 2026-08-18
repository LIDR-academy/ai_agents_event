#!/usr/bin/env python3
"""Measure SQL-side search latency over the five benchmark queries.

Live Session 08 — Block 2.1 (baseline before the HNSW index) and Block 2.2
(re-run right after creating it to expose the before/after gap).

Difference with ``query_examples.py``: that script goes through the HTTP
endpoint and its timing includes the OpenAI embedding round-trip, which the
index cannot influence. This one embeds each query ONCE upfront, then times
only the SQL execution (the part an index changes): 1 warm-up run (discarded)
plus 2 measured runs per query, reporting mean and deviation.

It reuses the project's async session factory and the RAG ``ChunkStore`` —
the exact same SELECT the ``/search`` endpoint runs.

Usage::

    docker compose exec ai-service python scripts/measure_baseline_s08.py
    # or, with the stack already up:
    docker compose exec ai-service python scripts/measure_baseline_s08.py

At the end it prints the first query's embedding as a pgvector literal — copy
it into ``scripts/sql_s08/02_test_antipatron.sql`` for the psql demos.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from s08_common import (  # noqa: E402
    EmbeddedQuery,
    Stopwatch,
    embed_benchmark_queries,
    format_table,
    latency_summary,
    require_embedder,
    truncate,
    vector_literal,
)

from app.foundation.persistence.database import get_async_session_factory  # noqa: E402
from app.generation.rag.store.repository import ChunkStore  # noqa: E402

K = 5
WARMUP_RUNS = 1
MEASURED_RUNS = 2


async def measure(queries: list[EmbeddedQuery]) -> None:
    session_factory = get_async_session_factory()
    store = ChunkStore()

    rows: list[list[str]] = []
    all_samples: list[float] = []

    async with session_factory() as session:
        for _, query, vector in queries:
            samples: list[float] = []
            result_count = 0
            for run in range(WARMUP_RUNS + MEASURED_RUNS):
                with Stopwatch() as timer:
                    results = await store.search(session, query_vector=vector, k=K)
                if run >= WARMUP_RUNS:  # discard warm-up
                    samples.append(timer.elapsed_ms)
                result_count = len(results)

            mean = statistics.fmean(samples)
            deviation = statistics.stdev(samples) if len(samples) > 1 else 0.0
            all_samples.extend(samples)
            rows.append([truncate(query, 40), f"{mean:.2f}", f"{deviation:.2f}", str(result_count)])

    print()
    print(format_table(["query", "mean_ms", "stdev_ms", "results"], rows))
    print()
    print(f"Summary ({len(all_samples)} measured runs): {latency_summary(all_samples)}")


def main() -> int:
    embedder = require_embedder()
    queries = embed_benchmark_queries(embedder)

    asyncio.run(measure(queries))

    # The antipattern demo (02_test_antipatron.sql) needs a real query vector
    # to paste into psql. Print the first one last so it sits at the bottom
    # of the scrollback, ready to copy.
    label, query, vector = queries[0]
    print()
    print(f'pgvector literal for query 1 ("{truncate(query, 60)}"):')
    print(vector_literal(vector))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
