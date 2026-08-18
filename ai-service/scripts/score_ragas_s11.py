#!/usr/bin/env python3
"""Standalone RAGAS scorer for pre-collected Session 11 samples.

This script imports ONLY ragas + langchain-openai + datasets — never the app —
so it runs in an isolated venv, decoupled from the project's bleeding-edge
langchain stack (which conflicts with ragas's hard ``langchain_community``
Vertex import). Collect the samples first with::

    DATABASE_URL=... uv run python scripts/eval_ragas_s11.py --collect-only samples.json

then score them here::

    /path/to/ragas-venv/bin/python scripts/score_ragas_s11.py samples.json --out report.json

ragas 0.4.x unconditionally imports ``langchain_community.chat_models.vertexai``
at module load; recent ``langchain-community`` dropped that path. We never use
Vertex (the judge is OpenAI), so we register lightweight stub modules before
importing ragas to satisfy the import without pulling the Vertex SDK.
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def _install_vertex_shims() -> None:
    """Satisfy ragas's hard Vertex imports without the Vertex SDK (unused here)."""
    try:  # If the real module exists, leave it alone.
        import langchain_community.chat_models.vertexai  # noqa: F401

        return
    except Exception:
        pass

    class _Unavailable:  # placeholder; never instantiated on the OpenAI path
        def __init__(self, *args, **kwargs):  # pragma: no cover - defensive
            raise RuntimeError("Vertex AI is not available in this scorer venv.")

    chat_mod = types.ModuleType("langchain_community.chat_models.vertexai")
    chat_mod.ChatVertexAI = _Unavailable
    sys.modules["langchain_community.chat_models.vertexai"] = chat_mod

    # Ensure `from langchain_community.llms import VertexAI` resolves too.
    try:
        import langchain_community.llms as llms_mod  # type: ignore

        if not hasattr(llms_mod, "VertexAI"):
            llms_mod.VertexAI = _Unavailable
    except Exception:
        llms_mod = types.ModuleType("langchain_community.llms")
        llms_mod.VertexAI = _Unavailable
        sys.modules["langchain_community.llms"] = llms_mod


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", help="JSON written by eval_ragas_s11.py --collect-only")
    parser.add_argument("--out", default=None, help="Write the scored report as JSON.")
    args = parser.parse_args()

    payload = json.loads(Path(args.samples).read_text(encoding="utf-8"))
    samples = payload["samples"] if isinstance(payload, dict) else payload
    judge_model = payload.get("judge_model", "gpt-4o-mini") if isinstance(payload, dict) else "gpt-4o-mini"
    embedding_model = (
        payload.get("embedding_model", "text-embedding-3-small")
        if isinstance(payload, dict)
        else "text-embedding-3-small"
    )

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

    print(f"Scoring {len(samples)} samples (judge={judge_model}, embeddings={embedding_model})...")
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge,
        embeddings=embeddings,
    )

    df = result.to_pandas()
    rows = []
    for i in range(len(samples)):
        rows.append(
            {m: (float(df.iloc[i][m]) if m in df.columns else float("nan")) for m in METRIC_NAMES}
        )
    table = _markdown_table([s["id"] for s in samples], rows)

    print("\n=== RAGAS generation baseline ===")
    print(table)

    if args.out:
        report = {
            "judge_model": judge_model,
            "embedding_model": embedding_model,
            "table_markdown": table,
            "per_query": [
                {"id": s["id"], **rows[i], "citation_report": s.get("citation_report")}
                for i, s in enumerate(samples)
            ],
        }
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
