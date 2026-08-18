"""Measure per-task hours coverage against the historical corpus — offline, cheap.

The demo's headline number is what fraction of the structure's tasks come back with
hours from ``estimate_task_hours``. Tuning that number by re-running the whole graph
costs a gpt-5 structure call per iteration and gives a different tree every time, so
this script freezes the tree and measures ONLY the retrieval + consensus step.

The trick that makes a sweep free: probe each task ONCE with no distance floor and a
wide ``top_k``, cache the full neighbourhood, then re-derive every ``(k, threshold)``
cell from that cached list. An 18-cell grid costs one embedding and one SQL query per
task instead of eighteen.

    python scripts/measure_task_hours_coverage.py \
        --structure data/evals/04_health_structure.json \
        --label baseline --sweep-k 3,5,8 \
        --sweep-threshold 0.35,0.40,0.45,0.50,0.55,0.60 \
        --per-task --worst 25 --json-out data/evals/reports/coverage_baseline.json

``--exact`` bypasses the probe and calls ``estimate_one`` verbatim per cell. Slower,
but it is the ground truth and it is the ONLY correct mode with ``--search-mode
hybrid`` or ``--rerank``, which reorder candidates so the probe shortcut breaks.

Run inside the container (it needs the DB and an embedder):

    docker compose exec ai-service python scripts/measure_task_hours_coverage.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domain.graph.agents._common import (  # noqa: E402
    LOW_RELIABILITY,
    modules_from_structure,
)
from app.generation.rag.retrieval.collections import Collection  # noqa: E402
from app.generation.rag.retrieval.pipeline import retrieve  # noqa: E402
from app.generation.rag.schemas import Chunk  # noqa: E402
from app.generation.rag.task_hours import (  # noqa: E402
    compose_task_search_text,
    distance_weighted_consensus,
    estimate_one,
)

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "evals" / "cache" / "query_embeddings.json"

# Probe parameters: no floor, wide k. Every swept cell is a subset of this.
PROBE_TOP_K = 30
PROBE_THRESHOLD = 2.0

# Mirrors business-backend/app/models/rag/task_hours_estimate_view.rb.
GREEN_RELIABILITY = 0.66

HIST_EDGES = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 2.01]


# --------------------------------------------------------------------------- query


def build_query_text(module: str, name: str, description: str | None, style: str) -> str:
    """The embedded query text under each experimental style.

    ``current`` is production (``task_hours.compose_task_search_text``). The others
    close, step by step, the gap with what the corpus actually embeds
    (``chunking/structural.py::render_component_text``): ``Component:`` instead of
    ``Task:``, then the project/sector header, then the trailing tech/complexity
    lines. None of them may fake ``Estimated hours:`` — it differs per chunk and
    would bias retrieval toward whatever number we invented.
    """
    if style == "current":
        return compose_task_search_text(module, name, description)

    body = []
    if module:
        body.append(f"Module: {module}")
    body.append(f"Component: {name}")
    if description:
        body.append(f"Description: {description}")

    if style == "component":
        return "\n".join(body)

    header = (
        "[Project: Clinic network platform covering scheduling, patient portal, "
        "interoperability and compliance]\n"
        "[Client sector: healthcare | Year: 2025 | Main tech: python_fastapi]\n"
    )
    if style == "header":
        return f"{header}\n" + "\n".join(body)
    if style == "full":
        tail = "Tech stack: python, postgresql\nComplexity: high"
        return f"{header}\n" + "\n".join(body) + f"\n{tail}"
    raise SystemExit(f"unknown --query-style {style!r}")


def flatten_tasks(structure: dict) -> list[dict]:
    """Frozen structure → flat ``[{module, name, description}]`` in tree order."""
    tasks: list[dict] = []
    for module in modules_from_structure(structure):
        for task in module["tasks"]:
            tasks.append(
                {
                    "module": module["name"] or "",
                    "name": task["name"],
                    "description": task.get("description"),
                }
            )
    return tasks


# ----------------------------------------------------------------------- embedding


def _cache_key(style: str, text: str) -> str:
    return hashlib.sha256(f"{style}\x00{text}".encode()).hexdigest()


def embed_queries(texts: list[str], style: str, *, use_cache: bool) -> list[list[float]]:
    """Embed every query text, batching misses into one ``embed_many`` call."""
    from app.dependencies import get_embedder

    embedder = get_embedder()
    if embedder is None:
        raise SystemExit("No embedder available — is OPENAI_API_KEY set?")

    cache: dict[str, list[float]] = {}
    if use_cache and CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())

    keys = [_cache_key(style, t) for t in texts]
    missing = [(k, t) for k, t in zip(keys, texts) if k not in cache]
    # Dedupe: two tasks can share a name+description.
    missing = list({k: (k, t) for k, t in missing}.values())

    if missing:
        chunks = [
            Chunk(chunk_id=k, text=t, metadata={}, token_count=max(1, len(t) // 4))
            for k, t in missing
        ]
        print(f"embedding {len(chunks)} new query texts (cache hits: {len(texts) - len(missing)})")
        for embedded in embedder.embed_many(chunks):
            cache[embedded.chunk_id] = embedded.embedding
        if use_cache:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(cache))
    else:
        print(f"embedding: {len(texts)} query texts all served from cache")

    return [cache[k] for k in keys]


# --------------------------------------------------------------------------- probe


async def probe_one(task: dict, text: str, embedding: list[float]) -> list[tuple[int, float, str]]:
    """Full neighbourhood for one task: ``[(hours, distance, budget_id)]``, closest first."""
    result = await retrieve(
        query_embedding=embedding,
        query_text=text,
        search_mode="vector",
        rerank=False,
        top_k=PROBE_TOP_K,
        recall_k=PROBE_TOP_K,
        rerank_top_n=PROBE_TOP_K,
        distance_threshold=PROBE_THRESHOLD,
        collection=Collection.BUDGET,
        chunk_types=["historical_task"],
    )
    return [
        (int(c.estimated_hours), c.distance, c.budget_id or "?")
        for c in result.chunks
        if c.estimated_hours is not None
    ]


async def probe_all(tasks: list[dict], texts: list[str], embeddings) -> list[list]:
    return await asyncio.gather(
        *(probe_one(t, x, e) for t, x, e in zip(tasks, texts, embeddings))
    )


# ------------------------------------------------------------------------ scoring


def score_from_probe(neighbourhood: list[tuple[int, float, str]], k: int, threshold: float) -> dict:
    """Re-derive one ``(k, threshold)`` cell from a cached neighbourhood."""
    usable = [(h, d) for h, d, _b in neighbourhood if d <= threshold][:k]
    if not usable:
        return {"has_match": False, "estimated_hours": None, "reliability": None,
                "dispersion": None, "neighbors": 0}
    hours, reliability, dispersion = distance_weighted_consensus(usable)
    return {
        "has_match": True,
        "estimated_hours": hours,
        "reliability": reliability,
        "dispersion": dispersion,
        "neighbors": len(usable),
    }


def band(row: dict) -> str:
    """Mirrors ``TaskHoursEstimateView#reliability_band``."""
    if not row["has_match"]:
        return "red"
    return "green" if (row["reliability"] or 0) >= GREEN_RELIABILITY else "amber"


def flag(row: dict) -> str | None:
    """Mirrors ``_common.flag_reason`` (``hours_range`` is always None on this path)."""
    if not row["has_match"]:
        return "no-match"
    if (row["reliability"] or 0) < LOW_RELIABILITY:
        return "low-rel"
    return None


def summarise(tasks: list[dict], rows: list[dict], k: int, threshold: float) -> dict:
    total = len(rows)
    matched = [r for r in rows if r["has_match"]]
    bands = [band(r) for r in rows]
    flags = [flag(r) for r in rows]
    rels = [r["reliability"] for r in matched if r["reliability"] is not None]
    disps = [r["dispersion"] for r in matched if r["dispersion"] is not None]
    return {
        "top_k": k,
        "threshold": threshold,
        "tasks": total,
        "matched": len(matched),
        "matched_pct": round(100.0 * len(matched) / total, 1) if total else 0.0,
        "green": bands.count("green"),
        "amber": bands.count("amber"),
        "red": bands.count("red"),
        "flagged": sum(1 for f in flags if f),
        "flagged_no_match": flags.count("no-match"),
        "flagged_low_rel": flags.count("low-rel"),
        "reliability_mean": round(statistics.fmean(rels), 3) if rels else None,
        "reliability_median": round(statistics.median(rels), 3) if rels else None,
        "reliability_max": round(max(rels), 3) if rels else None,
        "dispersion_mean": round(statistics.fmean(disps), 3) if disps else None,
        "dispersion_median": round(statistics.median(disps), 3) if disps else None,
    }


# ------------------------------------------------------------------------ printing


def print_summary(label: str, style: str, mode: str, s: dict) -> None:
    print(
        f"\n=== label={label}  style={style}  mode={mode}  "
        f"k={s['top_k']}  threshold={s['threshold']} ==="
    )
    print(f"tasks              {s['tasks']}")
    print(f"matched            {s['matched']}  ({s['matched_pct']}%)")
    print(f"  green (rel>={GREEN_RELIABILITY})  {s['green']:4d}")
    print(f"  amber              {s['amber']:4d}")
    print(f"  red   (no match)   {s['red']:4d}")
    print(
        f"flagged for recovery {s['flagged']}   "
        f"[no-match {s['flagged_no_match']} | low-rel {s['flagged_low_rel']}]"
    )
    print(
        f"reliability   mean {s['reliability_mean']}  p50 {s['reliability_median']}  "
        f"max {s['reliability_max']}"
    )
    print(f"dispersion    mean {s['dispersion_mean']}  p50 {s['dispersion_median']}")


def print_histogram(neighbourhoods: list[list], threshold: float) -> None:
    """Best-neighbour distance over ALL tasks, unfloored — what a threshold bump buys."""
    best = [min((d for _h, d, _b in n), default=None) for n in neighbourhoods]
    buckets = [0] * (len(HIST_EDGES) - 1)
    none_at_all = 0
    for b in best:
        if b is None:
            none_at_all += 1
            continue
        for i in range(len(HIST_EDGES) - 1):
            if HIST_EDGES[i] <= b < HIST_EDGES[i + 1]:
                buckets[i] += 1
                break
    print("best-neighbour distance histogram (ALL tasks, unfloored probe):")
    for i, count in enumerate(buckets):
        lo, hi = HIST_EDGES[i], HIST_EDGES[i + 1]
        marker = "  <-- first bucket above the current cut" if lo == threshold else ""
        label = f"  {lo:.2f}-{hi:.2f}" if hi <= 0.70 else f"  >={lo:.2f}   "
        print(f"{label} | {'#' * min(count, 60):<40} {count}{marker}")
    if none_at_all:
        print(f"  (no neighbour at all: {none_at_all})")


def print_worst(tasks, rows, neighbourhoods, n: int) -> None:
    ranked = sorted(
        range(len(tasks)),
        key=lambda i: min((d for _h, d, _b in neighbourhoods[i]), default=9.0),
        reverse=True,
    )
    print(f"\n--- {n} worst-grounded tasks (the template-authoring worklist) ---")
    for i in ranked[:n]:
        t, nb = tasks[i], neighbourhoods[i]
        best = f"{nb[0][1]:.3f} ({nb[0][2]})" if nb else "none"
        state = "MATCH" if rows[i]["has_match"] else "  --  "
        print(f"{state}  d={best:<26} {t['module']} :: {t['name']}")


# ---------------------------------------------------------------------------- main


async def run_exact(tasks, k, threshold, search_mode, rerank) -> list[dict]:
    async def one(t):
        est = await estimate_one(
            t["module"], t["name"], t["description"],
            top_k=k, distance_threshold=threshold,
            search_mode=search_mode, rerank=rerank,
        )
        return {
            "has_match": est.has_match,
            "estimated_hours": est.estimated_hours,
            "reliability": est.reliability,
            "dispersion": est.dispersion,
            "neighbors": len(est.neighbors),
        }

    return await asyncio.gather(*(one(t) for t in tasks))


def parse_floats(raw: str) -> list[float]:
    return [float(x) for x in raw.split(",") if x.strip()]


def parse_ints(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


async def main_async(args) -> None:
    structure = json.loads(Path(args.structure).read_text())
    tasks = flatten_tasks(structure)
    if not tasks:
        raise SystemExit(f"no tasks in {args.structure}")
    print(f"structure: {args.structure} — {len(tasks)} tasks")

    ks = parse_ints(args.sweep_k)
    thresholds = parse_floats(args.sweep_threshold)
    report = {
        "label": args.label,
        "structure": str(args.structure),
        "query_style": args.query_style,
        "search_mode": args.search_mode,
        "rerank": args.rerank,
        "exact": args.exact,
        "tasks": len(tasks),
        "cells": [],
    }

    if args.exact:
        for k in ks:
            for threshold in thresholds:
                rows = await run_exact(tasks, k, threshold, args.search_mode, args.rerank)
                s = summarise(tasks, rows, k, threshold)
                print_summary(args.label, "current", args.search_mode, s)
                report["cells"].append(s)
    else:
        if args.search_mode != "vector" or args.rerank:
            raise SystemExit(
                "--search-mode/--rerank change candidate ordering, so the probe shortcut "
                "is invalid. Re-run with --exact."
            )
        texts = [
            build_query_text(t["module"], t["name"], t["description"], args.query_style)
            for t in tasks
        ]
        embeddings = embed_queries(texts, args.query_style, use_cache=not args.no_cache)
        neighbourhoods = await probe_all(tasks, texts, embeddings)

        last_rows = None
        for k in ks:
            for threshold in thresholds:
                rows = [score_from_probe(n, k, threshold) for n in neighbourhoods]
                s = summarise(tasks, rows, k, threshold)
                print_summary(args.label, args.query_style, args.search_mode, s)
                if k == ks[0]:
                    print_histogram(neighbourhoods, threshold)
                report["cells"].append(s)
                last_rows = rows

        if args.per_task and last_rows is not None:
            print_worst(tasks, last_rows, neighbourhoods, args.worst)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {out}")


def quieten() -> None:
    """Drop the per-query retrieval INFO lines — 126 tasks would bury the report."""
    import structlog

    logging.basicConfig(level=logging.WARNING, force=True)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
        cache_logger_on_first_use=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--structure", required=True, help="Frozen AgentStructure JSON dump.")
    p.add_argument("--label", default="run", help="Name for this measurement.")
    p.add_argument("--sweep-k", default="5", help="Comma-separated top_k values.")
    p.add_argument("--sweep-threshold", default="0.45", help="Comma-separated distance cuts.")
    p.add_argument(
        "--query-style",
        default="current",
        choices=("current", "component", "header", "full"),
        help="Experimental query text shapes (see build_query_text).",
    )
    p.add_argument("--search-mode", default="vector", choices=("vector", "hybrid"))
    p.add_argument("--rerank", action="store_true")
    p.add_argument("--exact", action="store_true", help="Call estimate_one per cell (ground truth).")
    p.add_argument("--per-task", action="store_true")
    p.add_argument("--worst", type=int, default=20)
    p.add_argument("--json-out", default=None)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--verbose", action="store_true", help="Keep the retrieval INFO logs.")
    args = p.parse_args()
    if not args.verbose:
        quieten()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
