#!/usr/bin/env python3
"""Session 11 — two-mode generation-quality evaluation pipeline.

The Session 11 pre-work produced a RAGAS BASELINE (``evals/RAGAS_BASELINE_S11.md``).
This harness turns that one-off table into the two evaluation modes a real RAG
system runs, wiring the same golden set and the same four RAGAS metrics into:

* ``--gate`` — the OFFLINE REGRESSION GATE. Runs the golden set (which has a
  ``ground_truth``), scores all four metrics + the citation/hallucination checks,
  and compares each against the pre-work baseline. If any metric regresses beyond
  a tolerance, or a dangling citation appears, it prints FAIL and exits non-zero —
  the deploy/block decision, as data. (Same PASS/FAIL + exit-code shape as
  ``evals/run.py``.)
* ``--monitor`` — the REFERENCE-FREE PRODUCTION MONITOR. Real traffic has no
  ``ground_truth``, so only the two metrics that need none — ``faithfulness`` and
  ``answer_relevancy`` — run over a sample. No exit gating; it is a health read.

Both modes select a NAMED STAGE CONFIG (data, not code branches) that toggles the
Session 11 techniques — the hallucination gate, augmentation and synthesis — so
the live loop is *measure → flip a toggle → re-measure → deploy or block on the
delta*. ``--compare`` runs every config and prints the scoreboard.

Two-step run (same isolation trick as ``eval_ragas_s11.py``: collect in the
project venv with the stack up, score in a ragas-only venv)::

    # 1) collect samples through the real pipeline for a config
    uv run python scripts/eval_generation_s11.py --gate --config full --collect-only s.json
    # 2) score + gate in the isolated ragas venv
    /path/to/ragas-venv/bin/python scripts/eval_generation_s11.py --gate --score-file s.json

Single-shot (one venv with both stacks)::

    uv run python scripts/eval_generation_s11.py --gate --config full
    uv run python scripts/eval_generation_s11.py --monitor --config full
    uv run python scripts/eval_generation_s11.py --compare

**Goodhart's law**: any single metric, optimised alone, stops measuring quality —
faithfulness rewards copying, relevancy rewards restating the question. The gate
reads the four TOGETHER (two retrieval, two generation) precisely so no one number
can be gamed into a green light.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN_PATH = ROOT / "evals" / "golden_generation_s11.json"

# Baseline averages from the pre-work run (evals/RAGAS_BASELINE_S11.md). The gate
# fails a metric only when it drops MORE than the tolerance below its baseline —
# a technique must not make things worse to ship.
BASELINE = {
    "faithfulness": 0.552,
    "answer_relevancy": 0.033,
    "context_precision": 1.000,
    "context_recall": 0.114,
}
REGRESSION_TOLERANCE = 0.05
ALL_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
# The only metrics that need no ground_truth — usable on live, unlabelled traffic.
REFERENCE_FREE_METRICS = ("faithfulness", "answer_relevancy")


@dataclass(frozen=True)
class StageConfig:
    """A named toggle set for the Session 11 generation-quality stages."""

    id: str
    label: str
    gate: bool
    augment: bool
    synthesis: bool


CONFIGS: dict[str, StageConfig] = {
    "baseline": StageConfig("baseline", "Baseline (S10 pipeline)", False, False, False),
    "gate": StageConfig("gate", "+ Hallucination gate", True, False, False),
    "augment": StageConfig("augment", "+ Augmentation", True, True, False),
    "full": StageConfig("full", "+ Synthesis (full S11)", True, True, True),
}


def _apply_config(cfg: StageConfig) -> None:
    """Point the cached settings at this config's toggles for the collection run.

    Mutates the settings singleton in-process: the pipeline's ``effective_*``
    reads fall back to the settings default when no Redis override is set, so this
    changes behaviour on both host and container runs without touching .env."""
    from app.config import get_settings

    settings = get_settings()
    settings.HALLUCINATION_GATE_ENABLED = cfg.gate
    settings.AUGMENTATION_ENABLED = cfg.augment
    settings.SYNTHESIS_ENABLED = cfg.synthesis


async def collect_sample(item: dict, embedder, cfg: StageConfig) -> dict:
    """Run the real pipeline for one golden item under ``cfg`` and return a
    RAGAS-ready sample enriched with the citation + hallucination reports."""
    from app.config import get_settings
    from app.dependencies import get_token_encoder
    from app.generation.rag.context_assembler import build_context_block, truncate_to_token_budget
    from app.generation.rag.estimator import generate_estimate
    from app.generation.rag.query_reformulator import compose_search_text, reformulate_query
    from app.generation.rag.quality.augmentation import augment_chunks
    from app.generation.rag.quality.hallucination import gate_estimate
    from app.generation.rag.retrieval.pipeline import retrieve
    from app.generation.rag.validation import verify_citations
    from scripts.eval_ragas_s11 import GENERATION_THRESHOLD, render_estimate_text

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
    kept = truncate_to_token_budget(
        retrieval.chunks, settings.MAX_CONTEXT_TOKENS, get_token_encoder()
    )
    if cfg.augment:
        kept = augment_chunks(
            kept, compress=settings.AUGMENTATION_COMPRESS, reorder=settings.AUGMENTATION_REORDER
        )
    context_block = build_context_block(kept)
    estimate = await generate_estimate(context_block, structured_query=query)

    citation = verify_citations(estimate, {str(c.id) for c in kept})
    gate = None
    if cfg.gate:
        gate = await gate_estimate(
            estimate,
            kept,
            tolerance=settings.HALLUCINATION_NUMERIC_TOLERANCE,
            judge_model=settings.HALLUCINATION_JUDGE_MODEL,
        )

    return {
        "id": item["id"],
        "question": item["query"],
        "answer": render_estimate_text(estimate),
        "contexts": [c.content for c in kept],
        "ground_truth": item["ground_truth"],
        "citation_report": citation.model_dump(),
        "hallucination_report": gate.model_dump() if gate else None,
    }


def _score(samples: list[dict], metric_names, judge_model: str, embedding_model: str) -> list[dict]:
    """Score ``samples`` with the requested RAGAS metrics; version-tolerant."""
    from scripts.score_ragas_s11 import _install_vertex_shims

    # ragas 0.4.x imports ``langchain_community.chat_models.vertexai`` at load; the
    # project's langchain-community no longer ships it. Register the stub before
    # importing ragas (same trick as score_ragas_s11.py / eval_ragas_s11.py).
    _install_vertex_shims()
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

    registry = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
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
        metrics=[registry[m] for m in metric_names],
        llm=judge,
        embeddings=embeddings,
    )
    df = result.to_pandas()
    rows = []
    for i in range(len(samples)):
        rows.append(
            {m: (float(df.iloc[i][m]) if m in df.columns else float("nan")) for m in metric_names}
        )
    return rows


def _averages(rows: list[dict], metric_names) -> dict:
    return {m: sum(r[m] for r in rows) / len(rows) for m in metric_names}


def _citation_summary(samples: list[dict]) -> dict:
    dangling = sum(len(s["citation_report"]["dangling_citations"]) for s in samples)
    degraded = sum((s["hallucination_report"] or {}).get("degraded_lines", 0) for s in samples)
    return {"dangling_citations": dangling, "degraded_lines": degraded}


def run_gate(samples: list[dict], judge_model: str, embedding_model: str) -> int:
    """Offline regression gate: score all four metrics + citations, PASS/FAIL."""
    rows = _score(samples, ALL_METRICS, judge_model, embedding_model)
    avg = _averages(rows, ALL_METRICS)
    cites = _citation_summary(samples)

    print("\n=== Offline regression gate (golden set + ground_truth) ===")
    print(f"{'metric':<20} {'baseline':>10} {'now':>8} {'floor':>8}  verdict")
    failed = False
    for m in ALL_METRICS:
        floor = BASELINE[m] - REGRESSION_TOLERANCE
        ok = avg[m] >= floor
        failed = failed or not ok
        print(
            f"{m:<20} {BASELINE[m]:>10.3f} {avg[m]:>8.3f} {floor:>8.3f}  {'PASS' if ok else 'FAIL'}"
        )
    # A dangling citation is a hard fail regardless of metric deltas.
    cite_ok = cites["dangling_citations"] == 0
    failed = failed or not cite_ok
    print(
        f"{'dangling_citations':<20} {0:>10} {cites['dangling_citations']:>8} {0:>8}  "
        f"{'PASS' if cite_ok else 'FAIL'}"
    )
    print(f"(degraded lines flagged by the gate: {cites['degraded_lines']})")
    verdict = "BLOCK" if failed else "DEPLOY"
    print(f"\nGATE: {verdict}")
    return 1 if failed else 0


def run_monitor(samples: list[dict], judge_model: str, embedding_model: str) -> int:
    """Reference-free production monitor: faithfulness + relevancy on a sample."""
    rows = _score(samples, REFERENCE_FREE_METRICS, judge_model, embedding_model)
    avg = _averages(rows, REFERENCE_FREE_METRICS)
    cites = _citation_summary(samples)

    print("\n=== Reference-free production monitor (no ground_truth) ===")
    for m in REFERENCE_FREE_METRICS:
        print(f"{m:<20} {avg[m]:>8.3f}")
    print(f"{'dangling_citations':<20} {cites['dangling_citations']:>8}")
    print(f"{'degraded_lines':<20} {cites['degraded_lines']:>8}")
    print("\n(no exit gating — this is a health read on live, unlabelled traffic)")
    return 0


def _print_compare(board: list[tuple[StageConfig, dict, dict]]) -> None:
    print("\n=== Config scoreboard (measure → improve → measure) ===")
    header = (
        f"{'config':<26}"
        + "".join(f"{m[:12]:>13}" for m in ALL_METRICS)
        + f"{'dangling':>10}{'degraded':>10}"
    )
    print(header)
    for cfg, avg, cites in board:
        cells = "".join(f"{avg[m]:>13.3f}" for m in ALL_METRICS)
        print(
            f"{cfg.label:<26}{cells}{cites['dangling_citations']:>10}{cites['degraded_lines']:>10}"
        )


async def _collect(items: list[dict], cfg: StageConfig) -> list[dict]:
    from scripts.s08_common import require_embedder

    _apply_config(cfg)
    embedder = require_embedder()
    print(f"Collecting {len(items)} samples · config={cfg.id} ({cfg.label})...")
    samples = []
    for item in items:
        print(f"  - {item['id']}: {item['query'][:60]}...")
        samples.append(await collect_sample(item, embedder, cfg))
    return samples


async def _amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gate", action="store_true", help="Offline regression gate (exit code).")
    mode.add_argument("--monitor", action="store_true", help="Reference-free production monitor.")
    mode.add_argument("--compare", action="store_true", help="Run every config and print a board.")
    parser.add_argument("--config", choices=list(CONFIGS), default="full", help="Stage config.")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N queries.")
    parser.add_argument("--collect-only", metavar="PATH", help="Dump samples, do not score.")
    parser.add_argument("--score-file", metavar="PATH", help="Skip collection; score this file.")
    args = parser.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    judge_model = golden.get("judge_model", "gpt-4o-mini")
    embedding_model = golden.get("embedding_model", "text-embedding-3-small")

    if args.score_file:
        payload = json.loads(Path(args.score_file).read_text(encoding="utf-8"))
        samples = payload["samples"] if isinstance(payload, dict) else payload
        if args.monitor:
            return run_monitor(samples, judge_model, embedding_model)
        return run_gate(samples, judge_model, embedding_model)

    items = golden["queries"][: args.limit] if args.limit else golden["queries"]

    if args.compare:
        board = []
        for cfg in CONFIGS.values():
            samples = await _collect(items, cfg)
            rows = _score(samples, ALL_METRICS, judge_model, embedding_model)
            board.append((cfg, _averages(rows, ALL_METRICS), _citation_summary(samples)))
        _print_compare(board)
        return 0

    cfg = CONFIGS[args.config]
    samples = await _collect(items, cfg)
    if args.collect_only:
        Path(args.collect_only).write_text(
            json.dumps(
                {
                    "judge_model": judge_model,
                    "embedding_model": embedding_model,
                    "config": cfg.id,
                    "samples": samples,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {len(samples)} samples to {args.collect_only} (score with --score-file).")
        return 0

    if args.monitor:
        return run_monitor(samples, judge_model, embedding_model)
    return run_gate(samples, judge_model, embedding_model)


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
