#!/usr/bin/env python3
"""Compare chunking strategies over the budget corpus.

Loads ``data/budgets_sample.json`` + ``data/test_queries.json``, runs the
selected chunkers through the in-memory comparison framework, and prints corpus
stats and/or the top-k chunks each strategy retrieves per query. With
``--output`` it writes a Markdown report (the mentor's pre-flight safety net).

This is the tool the mentor drives during the Session 7 demos. Nothing is
persisted; everything runs in memory.

Examples::

    uv run python scripts/compare_chunkers.py --strategies all --queries all --show-stats
    uv run python scripts/compare_chunkers.py --strategies recursive --show-stats
    uv run python scripts/compare_chunkers.py --strategies sentence-window,structural \\
        --queries "OAuth authentication for fintech mobile app" --show-top-k 3
    uv run python scripts/compare_chunkers.py --models small-1536,small-768
    uv run python scripts/compare_chunkers.py --strategies all --queries all \\
        --show-stats --show-cost --output app/embedding_pipeline/COMPARISON_REPORT.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json  # noqa: E402

from openai import OpenAI  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.dependencies import ALL_STRATEGIES, build_chunkers  # noqa: E402
from app.generation.rag.analysis.comparison import ChunkingComparator  # noqa: E402
from app.generation.rag.embedding.embedder import OpenAIEmbedder  # noqa: E402
from app.generation.rag.schemas import Budget  # noqa: E402
from app.generation.rag.analysis.similarity import cosine_similarity  # noqa: E402

DATA_DIR = ROOT / "data"
BUDGETS_PATH = DATA_DIR / "budgets_sample.json"
QUERIES_PATH = DATA_DIR / "test_queries.json"

# Friendly aliases so hyphenated names from the guide map onto the registry.
_ALIASES = {"fixed": "fixed_size"}

# Embedding model variants for --models (OpenAI only; no local models).
MODEL_VARIANTS = {
    "small-1536": ("text-embedding-3-small", None),
    "small-768": ("text-embedding-3-small", 768),
}


def _normalize(name: str) -> str:
    norm = name.strip().replace("-", "_")
    return _ALIASES.get(norm, norm)


def load_budgets(limit: int | None) -> list[Budget]:
    raw = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    budgets = [Budget(**b) for b in raw]
    return budgets[:limit] if limit else budgets


def load_queries(arg: str) -> list[str]:
    if arg == "all":
        return json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    return [q.strip() for q in arg.split(",") if q.strip()]


def parse_strategies(arg: str) -> list[str]:
    if arg == "all":
        return list(ALL_STRATEGIES)
    return [_normalize(s) for s in arg.split(",") if s.strip()]


def build_embedder(dimensions: int | None = None) -> OpenAIEmbedder:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise SystemExit("ERROR: OPENAI_API_KEY is not set (check your .env).")
    return OpenAIEmbedder(
        client=OpenAI(api_key=settings.OPENAI_API_KEY),
        model=settings.EMBEDDING_MODEL,
        dimensions=dimensions,
    )


# --- pretty printing -------------------------------------------------------


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
    return f"{line}\n{sep}\n{body}"


def stats_rows(stats: dict, show_cost: bool) -> tuple[list[str], list[list[str]]]:
    headers = ["strategy", "n_chunks", "min", "p50", "p95", "max", "orphans", "obese"]
    if show_cost:
        headers += ["cost_usd", "seconds"]
    rows = []
    for name, s in stats.items():
        td = s.token_distribution
        row = [
            name,
            str(s.n_chunks),
            str(td.min),
            str(td.p50),
            str(td.p95),
            str(td.max),
            str(s.n_orphan_chunks),
            str(s.n_obese_chunks),
        ]
        if show_cost:
            row += [f"{s.ingestion_cost_usd:.6f}", f"{s.ingestion_seconds:.2f}"]
        rows.append(row)
    return headers, rows


def print_top_k(queries: dict, resolve_parents: bool, budgets: list[Budget]) -> None:
    parent_text = _parent_index(budgets) if resolve_parents else {}
    for strategy, results in queries.items():
        print(f"\n=== {strategy} ===")
        for qr in results:
            print(f"\nQuery: {qr.query}")
            for rank, top in enumerate(qr.top_k, 1):
                print(f"  {rank}. [{top.cosine:.4f}] {top.chunk_id}")
                print(f"     {top.text_preview}")
                if resolve_parents and "::" in top.chunk_id:
                    parent_id = f"{top.chunk_id.split('::')[0]}::parent"
                    if parent_id in parent_text and parent_id != top.chunk_id:
                        print(f"     ↑ parent {parent_id}: {parent_text[parent_id][:120]}…")


def _parent_index(budgets: list[Budget]) -> dict[str, str]:
    from app.generation.rag.chunking.structural import serialize_budget

    return {f"{b.budget_id}::parent": serialize_budget(b) for b in budgets}


def top1_cosine_avg(results: list) -> float:
    tops = [qr.top_k[0].cosine for qr in results if qr.top_k]
    return sum(tops) / len(tops) if tops else 0.0


# --- modes -----------------------------------------------------------------


def run_models(model_arg: str, budgets: list[Budget]) -> None:
    from app.generation.rag.chunking.structural import render_component_text

    variants = [v.strip() for v in model_arg.split(",") if v.strip()]
    # A few components to measure: two similar (auth) + one unrelated.
    comps = []
    for b in budgets:
        for c in b.components:
            comps.append((b, c))
    sample = comps[:3]
    texts = [render_component_text(b, c) for b, c in sample]
    print("Model comparison (latency per embedding + cosine of first two components):\n")
    headers = ["model", "dims", "avg_latency_ms", "cosine(c0,c1)"]
    rows = []
    for variant in variants:
        if variant not in MODEL_VARIANTS:
            print(f"  (skipping unknown model variant: {variant})")
            continue
        model, dims = MODEL_VARIANTS[variant]
        embedder = build_embedder(dimensions=dims)
        latencies = []
        vectors = []
        for text in texts:
            t0 = time.perf_counter()
            vectors.append(embedder.embed_one(text))
            latencies.append((time.perf_counter() - t0) * 1000)
        cos = cosine_similarity(vectors[0], vectors[1])
        rows.append(
            [
                variant,
                str(dims or 1536),
                f"{sum(latencies) / len(latencies):.1f}",
                f"{cos:.4f}",
            ]
        )
    print(format_table(headers, rows))


def write_report(path: Path, stats: dict, queries: dict) -> None:
    lines = [
        "# Chunking comparison report",
        "",
        "> Generado por `scripts/compare_chunkers.py --output ...` como red de "
        "seguridad del directo. Números reales del corpus instrumentado.",
        "",
        "## Estadísticos por estrategia",
        "",
        "| strategy | n_chunks | min | p50 | p95 | max | orphans (<20) | obese (>800) | "
        "cost_usd | seconds |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in stats.items():
        td = s.token_distribution
        lines.append(
            f"| {name} | {s.n_chunks} | {td.min} | {td.p50} | {td.p95} | {td.max} | "
            f"{s.n_orphan_chunks} | {s.n_obese_chunks} | {s.ingestion_cost_usd:.6f} | "
            f"{s.ingestion_seconds:.2f} |"
        )
    if queries:
        lines += ["", "## Top-k por consulta y estrategia", ""]
        # Group by query for readability.
        all_queries = [qr.query for qr in next(iter(queries.values()))]
        for query in all_queries:
            lines += [f"### {query}", ""]
            for name, results in queries.items():
                qr = next((r for r in results if r.query == query), None)
                if not qr:
                    continue
                tops = " · ".join(f"{t.chunk_id} ({t.cosine:.3f})" for t in qr.top_k)
                lines.append(f"- **{name}**: {tops}")
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare chunking strategies.")
    parser.add_argument("--strategies", default="all", help="'all' or comma list of names.")
    parser.add_argument("--queries", default="", help="'all', comma list, or empty.")
    parser.add_argument("--show-stats", action="store_true")
    parser.add_argument("--show-top-k", type=int, default=0, metavar="N")
    parser.add_argument("--show-cost", action="store_true")
    parser.add_argument("--output-table", action="store_true", help="Compact stats+top1 table.")
    parser.add_argument("--output", type=Path, default=None, help="Write a Markdown report.")
    parser.add_argument("--limit-budgets", type=int, default=None)
    parser.add_argument("--resolve-parents", action="store_true")
    parser.add_argument("--models", default=None, help="e.g. small-1536,small-768")
    args = parser.parse_args()

    budgets = load_budgets(args.limit_budgets)

    # Model-comparison mode is independent of chunking.
    if args.models:
        run_models(args.models, budgets)
        return 0

    strategies = parse_strategies(args.strategies)
    try:
        chunkers = build_chunkers(strategies)
    except KeyError as exc:
        raise SystemExit(f"Unknown strategy: {exc.args[0]}. Available: {ALL_STRATEGIES}")
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    embedder = build_embedder()
    comparator = ChunkingComparator(chunkers, embedder)

    queries = load_queries(args.queries) if (args.queries or args.show_top_k or args.output) else []
    top_k = args.show_top_k or 3

    stats = comparator.compute_stats(budgets)
    query_results = comparator.run_queries(budgets, queries, top_k) if queries else {}

    if args.output_table:
        headers = ["strategy", "n_chunks", "p50_tokens", "top1_cosine_avg"]
        rows = []
        for name, s in stats.items():
            avg = top1_cosine_avg(query_results.get(name, []))
            rows.append([name, str(s.n_chunks), str(s.token_distribution.p50), f"{avg:.4f}"])
        print(format_table(headers, rows))

    if args.show_stats and not args.output_table:
        headers, rows = stats_rows(stats, args.show_cost)
        print(format_table(headers, rows))

    if args.show_top_k or (queries and not args.output_table and not args.show_stats):
        print_top_k(query_results, args.resolve_parents, budgets)

    if args.output:
        write_report(args.output, stats, query_results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
