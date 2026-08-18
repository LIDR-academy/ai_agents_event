#!/usr/bin/env python3
"""RAGAS generation-quality baseline for the Session 11 golden set.

For each query in ``evals/golden_generation_s11.json`` this runs the REAL
RAG generation pipeline (reformulate -> embed -> retrieve -> assemble ->
generate) and records the four inputs RAGAS needs:

    question      -> the estimation request (golden ``query``)
    answer        -> the estimate the pipeline generated, rendered as text
    contexts      -> the retrieved chunks placed in the context block
    ground_truth  -> the expert reference estimate (golden ``ground_truth``)

It then computes the four RAGAS metrics — faithfulness, answer_relevancy,
context_precision, context_recall — with an OpenAI chat model as judge and
``text-embedding-3-small`` for the embedding-based metrics, and prints a table
with one row per query plus an average row. The table is the generation-quality
BASELINE brought to the live session.

Prerequisites (host run):
    * ai-service/.env with OPENAI_API_KEY
    * the stack up and the budget corpus ingested
      (uv run python scripts/query_examples.py ingests data/budgets_sample.json)
    * dev deps installed: uv sync  (pulls ragas + datasets)

Usage::

    uv run python scripts/eval_ragas_s11.py
    uv run python scripts/eval_ragas_s11.py --limit 2 --out evals/ragas_baseline_s11.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.s08_common import require_embedder  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.generation.rag.context_assembler import (  # noqa: E402
    build_context_block,
    truncate_to_token_budget,
)
from app.generation.rag.estimator import generate_estimate  # noqa: E402
from app.generation.rag.query_reformulator import (  # noqa: E402
    compose_search_text,
    reformulate_query,
)
from app.generation.rag.retrieval.pipeline import retrieve  # noqa: E402
from app.generation.rag.schemas import Estimate  # noqa: E402
from app.generation.rag.validation import verify_citations  # noqa: E402

GOLDEN_PATH = ROOT / "evals" / "golden_generation_s11.json"
# Permissive threshold: we want the generator to see context for every query so
# RAGAS grades GENERATION, not the soft-fail gate.
GENERATION_THRESHOLD = 1.2
METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def render_estimate_text(estimate: Estimate) -> str:
    """Render an :class:`Estimate` as the plain-text 'answer' RAGAS scores."""
    if estimate.confidence == "insufficient":
        return (
            "Insufficient context to produce an estimate. "
            f"{estimate.insufficient_context_explanation or ''}"
        ).strip()

    lines: list[str] = []
    if estimate.total_engineer_days is not None:
        lines.append(f"Total: {estimate.total_engineer_days} engineer-days.")
    if estimate.duration_weeks is not None:
        lines.append(f"Duration: {estimate.duration_weeks} weeks.")
    lines.append(f"Confidence: {estimate.confidence}.")
    for module in estimate.modules:
        lines.append(f"Module {module.name}:")
        for task in module.tasks:
            days = "n/a" if task.engineer_days is None else f"{task.engineer_days}d"
            tag = "grounded" if task.grounded else "no-data"
            lines.append(f"  - {task.name}: {days} [{tag}]")
    if estimate.assumptions:
        lines.append("Assumptions: " + "; ".join(a.description for a in estimate.assumptions))
    lines.append(f"Reasoning: {estimate.reasoning}")
    return "\n".join(lines)


async def collect_sample(item: dict, embedder) -> dict:
    """Run the pipeline for one golden item and return a RAGAS-ready sample."""
    settings = get_settings()
    transcript = item.get("transcript") or item["query"]

    query = await reformulate_query(transcript)
    search_text = compose_search_text(query)
    embedding = await asyncio.to_thread(embedder.embed_one, search_text)

    retrieval = await retrieve(
        query_embedding=embedding,
        query_text=search_text,
        search_mode="hybrid",
        rerank=True,
        top_k=settings.RETRIEVAL_TOP_K,
        recall_k=settings.RETRIEVAL_RECALL_TOP_K,
        rerank_top_n=settings.RERANK_TOP_N,
        distance_threshold=GENERATION_THRESHOLD,
        rrf_k=settings.RRF_K,
    )

    from app.dependencies import get_token_encoder

    kept = truncate_to_token_budget(
        retrieval.chunks, settings.MAX_CONTEXT_TOKENS, get_token_encoder()
    )
    context_block = build_context_block(kept)
    estimate = await generate_estimate(context_block, structured_query=query)
    report = verify_citations(estimate, {str(chunk.id) for chunk in kept})

    return {
        "id": item["id"],
        "question": item["query"],
        "answer": render_estimate_text(estimate),
        "contexts": [chunk.content for chunk in kept],
        "ground_truth": item["ground_truth"],
        "citation_report": report.model_dump(),
    }


def run_ragas(samples: list[dict], judge_model: str, embedding_model: str):
    """Compute the four RAGAS metrics over the collected samples."""
    from datasets import Dataset
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    dataset = Dataset.from_list(
        [
            {
                "question": s["question"],
                "answer": s["answer"],
                "contexts": s["contexts"],
                "ground_truth": s["ground_truth"],
            }
            for s in samples
        ]
    )
    judge = LangchainLLMWrapper(ChatOpenAI(model=judge_model, temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=embedding_model))
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge,
        embeddings=embeddings,
    )
    return result


def _per_query_scores(result, n: int) -> list[dict]:
    """Extract per-query metric scores from a RAGAS result, version-tolerant."""
    df = result.to_pandas()
    rows = []
    for i in range(n):
        row = {}
        for metric in METRIC_NAMES:
            row[metric] = float(df.iloc[i][metric]) if metric in df.columns else float("nan")
        rows.append(row)
    return rows


def _markdown_table(ids: list[str], rows: list[dict]) -> str:
    header = "| query | " + " | ".join(METRIC_NAMES) + " |"
    sep = "|" + "---|" * (len(METRIC_NAMES) + 1)
    body = []
    for qid, row in zip(ids, rows):
        cells = " | ".join(f"{row[m]:.3f}" for m in METRIC_NAMES)
        body.append(f"| {qid} | {cells} |")
    avg = {m: sum(r[m] for r in rows) / len(rows) for m in METRIC_NAMES}
    avg_cells = " | ".join(f"{avg[m]:.3f}" for m in METRIC_NAMES)
    body.append(f"| **average** | {avg_cells} |")
    return "\n".join([header, sep, *body])


def score_samples(samples: list[dict], judge_model: str, embedding_model: str, out: str | None):
    """Score pre-collected samples with RAGAS and print/persist the table.

    Split out so it can run in an ISOLATED venv (ragas + langchain-openai only),
    decoupled from the app's heavy dependency tree — the project's bleeding-edge
    langchain stack conflicts with ragas's hard vertexai import.
    """
    result = run_ragas(samples, judge_model, embedding_model)
    rows = _per_query_scores(result, len(samples))
    table = _markdown_table([s["id"] for s in samples], rows)

    print("\n=== RAGAS generation baseline ===")
    print(table)

    if out:
        report = {
            "judge_model": judge_model,
            "embedding_model": embedding_model,
            "table_markdown": table,
            "per_query": [
                {"id": s["id"], **rows[i], "citation_report": s.get("citation_report")}
                for i, s in enumerate(samples)
            ],
        }
        Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {out}")


async def _amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Only the first N queries.")
    parser.add_argument("--out", type=str, default=None, help="Write the scored report as JSON.")
    parser.add_argument(
        "--collect-only",
        type=str,
        default=None,
        metavar="PATH",
        help="Only run the app pipeline and dump samples to PATH (no RAGAS). Use this "
        "in the project venv, then score the file in an isolated venv with --score-file.",
    )
    parser.add_argument(
        "--score-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Skip collection; load samples from PATH and only run RAGAS scoring.",
    )
    args = parser.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    judge_model = golden.get("judge_model", "gpt-4o-mini")
    embedding_model = golden.get("embedding_model", "text-embedding-3-small")

    # Score-only path: no app imports exercised beyond what is already loaded.
    if args.score_file:
        payload = json.loads(Path(args.score_file).read_text(encoding="utf-8"))
        samples = payload["samples"] if isinstance(payload, dict) else payload
        score_samples(samples, judge_model, embedding_model, args.out)
        return 0

    items = golden["queries"][: args.limit] if args.limit else golden["queries"]
    embedder = require_embedder()

    print(f"Collecting {len(items)} samples through the generation pipeline...")
    samples = []
    for item in items:
        print(f"  - {item['id']}: {item['query'][:70]}...")
        samples.append(await collect_sample(item, embedder))

    if args.collect_only:
        Path(args.collect_only).write_text(
            json.dumps(
                {
                    "judge_model": judge_model,
                    "embedding_model": embedding_model,
                    "samples": samples,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {len(samples)} samples to {args.collect_only} (run --score-file to score).")
        return 0

    print(f"\nScoring with RAGAS (judge={judge_model}, embeddings={embedding_model})...")
    score_samples(samples, judge_model, embedding_model, args.out)
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
