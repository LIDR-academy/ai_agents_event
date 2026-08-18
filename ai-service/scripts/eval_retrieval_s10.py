#!/usr/bin/env python3
"""Measure retrieval quality across NAMED Session 10 configurations.

Runs the golden set (``evals/golden_retrieval.json``) through the advanced
retrieval pipeline for each named configuration and reports precision@k (mean
over the queries) and end-to-end latency (mean over measured runs), plus a
per-query precision breakdown.

The configurations are DATA, not code branches: each is a ``StageConfig`` that
turns specific stages on/off (the four pre-work baselines A–D, then routing,
query transform, temporal decay and the full pipeline). Add a row by appending a
``NamedConfig`` — no new code path.

Method notes:
* A permissive distance threshold is used so the top-k is never truncated by the
  relevance floor — we are comparing RANKING quality, not the soft-fail gate.
* A retrieved chunk is relevant iff its ``source_id`` (budget_id / transcript_id /
  doc_id) is in the query's ``relevant_source_ids`` (``relevant_budget_ids`` is
  still honoured for the original budget-only queries). precision@k = hits / k.
* Latency is END-TO-END retrieval: unlike the pre-work harness it INCLUDES the
  per-sub-query embedding and, for the routing/transform rows, the LLM calls those
  techniques inherently add — that overhead IS the cost the trade-off weighs.

Usage (host, stack up + all three collections ingested + OPENAI_API_KEY)::

    uv run python scripts/eval_retrieval_s10.py

Ingest first: ``scripts/query_examples.py`` (budgets) +
``scripts/build_multi_index_corpus.py`` (transcripts + technical docs).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.s08_common import Stopwatch, require_embedder  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.dependencies import get_reranker  # noqa: E402
from app.generation.rag.retrieval.advanced_pipeline import (  # noqa: E402
    StageConfig,
    advanced_retrieve,
)
from app.generation.rag.retrieval.collections import Collection  # noqa: E402

GOLDEN_PATH = ROOT / "evals" / "golden_retrieval.json"

# No effective relevance floor: we want a full top-k to grade ranking quality.
NO_FLOOR_THRESHOLD = 2.0
# Latency sampling per (query, config): one discarded warm-up + N measured runs.
MEASURED_RUNS = 3
# Budgets only, for the A–D baselines (keeps them comparable to the pre-work table).
BUDGET_ONLY = [Collection.BUDGET]


@dataclass(frozen=True)
class NamedConfig:
    """One measured configuration: a label + the stage toggles + routing scope."""

    id: str
    label: str
    stages: StageConfig
    explicit: list[Collection] | None  # None = let the router decide (multi-index)


def _stages(**overrides) -> StageConfig:
    """Build a StageConfig with the harness defaults (permissive threshold, k=5)."""
    base = dict(
        routing_enabled=False,
        query_transform_enabled=False,
        search_mode="vector",
        rerank=False,
        temporal_decay_enabled=False,
        top_k=5,
        distance_threshold=NO_FLOOR_THRESHOLD,
    )
    base.update(overrides)
    return StageConfig(**base)


# The named configurations (DATA). A–D reproduce the pre-work baselines on the
# budgets collection; E–H add the live-session techniques.
CONFIGS: list[NamedConfig] = [
    NamedConfig("A", "Vector", _stages(search_mode="vector"), BUDGET_ONLY),
    NamedConfig("B", "Hybrid", _stages(search_mode="hybrid"), BUDGET_ONLY),
    NamedConfig("C", "Vector+Rerank", _stages(search_mode="vector", rerank=True), BUDGET_ONLY),
    NamedConfig("D", "Hybrid+Rerank", _stages(search_mode="hybrid", rerank=True), BUDGET_ONLY),
    NamedConfig(
        "E",
        "Hybrid+Rerank+Temporal",
        _stages(search_mode="hybrid", rerank=True, temporal_decay_enabled=True),
        BUDGET_ONLY,
    ),
    NamedConfig(
        "F",
        "Multi-index routing",
        _stages(search_mode="hybrid", rerank=True, routing_enabled=True),
        None,
    ),
    NamedConfig(
        "G",
        "Query transform",
        _stages(search_mode="hybrid", rerank=True, query_transform_enabled=True),
        BUDGET_ONLY,
    ),
    NamedConfig(
        "H",
        "Full pipeline",
        _stages(
            search_mode="hybrid",
            rerank=True,
            routing_enabled=True,
            query_transform_enabled=True,
            temporal_decay_enabled=True,
        ),
        None,
    ),
]


def relevant_ids(query: dict) -> set[str]:
    """Annotated relevant document ids (generic source ids, budget ids fallback)."""
    return set(query.get("relevant_source_ids") or query.get("relevant_budget_ids") or [])


def precision_at_k(chunks, relevant: set[str], k: int) -> float:
    """Fraction of the top-k results whose source document is genuinely relevant."""
    top = chunks[:k]
    hits = sum(1 for chunk in top if (chunk.source_id or chunk.budget_id) in relevant)
    return hits / k


async def _run_once(cfg: NamedConfig, query_text: str, embedder, reference_date: date):
    outcome = await advanced_retrieve(
        query_text=query_text,
        embedder=embedder,
        stages=cfg.stages,
        explicit_collections=cfg.explicit,
        reference_date=reference_date,
    )
    return outcome.chunks


async def main() -> int:
    get_settings()
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    queries = golden["queries"]
    k = int(golden.get("k", 5))
    reference_date = date.today()

    embedder = require_embedder()
    print("Warming up the cross-encoder (first load downloads weights)...")
    get_reranker().load()

    results = {cfg.id: {"precisions": [], "latencies_ms": [], "per_query": {}} for cfg in CONFIGS}
    empty_warning = False

    for cfg in CONFIGS:
        for q in queries:
            relevant = relevant_ids(q)

            # Warm-up (discarded) then measured runs.
            await _run_once(cfg, q["query"], embedder, reference_date)
            samples = []
            last = None
            for _ in range(MEASURED_RUNS):
                with Stopwatch() as sw:
                    last = await _run_once(cfg, q["query"], embedder, reference_date)
                samples.append(sw.elapsed_ms)

            if not last:
                empty_warning = True
            precision = precision_at_k(last, relevant, k)
            results[cfg.id]["precisions"].append(precision)
            results[cfg.id]["latencies_ms"].extend(samples)
            results[cfg.id]["per_query"][q["id"]] = precision

    _print_report(results, queries, k)
    if empty_warning:
        print(
            "\nWARNING: some configurations returned 0 chunks. Are all three "
            "collections ingested? Run scripts/query_examples.py and "
            "scripts/build_multi_index_corpus.py.",
            file=sys.stderr,
        )
    return 0


def _print_report(results: dict, queries: list, k: int) -> None:
    print(f"\n## Retrieval evaluation — precision@{k} and latency\n")
    print(f"| Config | Stages | Precision@{k} | Latency (ms) |")
    print("| --- | --- | --- | --- |")
    for cfg in CONFIGS:
        bucket = results[cfg.id]
        mean_p = statistics.fmean(bucket["precisions"])
        mean_l = statistics.fmean(bucket["latencies_ms"])
        print(f"| {cfg.id} | {cfg.label} | {mean_p:.2f} | {mean_l:.1f} |")

    print(f"\n### Per-query precision@{k}\n")
    print("| Query | " + " | ".join(cfg.id for cfg in CONFIGS) + " |")
    print("| --- | " + " | ".join("---" for _ in CONFIGS) + " |")
    for q in queries:
        row = [q["id"]] + [f"{results[cfg.id]['per_query'][q['id']]:.2f}" for cfg in CONFIGS]
        print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
