#!/usr/bin/env python3
"""Session 12 — run the hand-written estimation agent over a transcript.

The agent drives the two wizard phases around the human review gate. This CLI
shows both loops end to end (auto-approving the structure in between, so there is
no interactive pause):

1. **Phase 1 — structure**: the agent decomposes the brief into modules→tasks
   (no tools, no hours). Prints the one-step trace + the tree.
2. **Phase 2 — hours recovery**: the reason→act→observe loop searches historical
   analogs for each task and derives hours with the deterministic consensus.
   Prints the STEP N trace + the derived hours.

(The live wizard runs a deterministic per-task pass first and only sends the
UNGROUNDED tasks to phase 2; this CLI sends every task so the loop is always
exercised — that is the point of the deliverable trace.)

Cost discipline (from the statement): debug the LOOP MECHANICS cheaply first with
``gpt-5-mini`` + ``--stub``, then switch to ``gpt-5`` / ``medium`` for the real run.

    # 1) offline loop debugging with the student stub (NO database needed)
    uv run python scripts/run_agent_s12.py \\
        exercises/session-12/sample_transcript_simple.txt --model gpt-5-mini --stub

    # 2) the real run (needs the stack up + task corpus ingested)
    docker compose exec ai-service python scripts/run_agent_s12.py \\
        exercises/session-12/sample_transcript_complex.txt --model gpt-5 --effort medium \\
        --out exercises/session-12/example_trace_complex.txt

``search_budgets`` wraps the real S9/S10 ``retrieve()`` pipeline by default, so the
real runs need the stack up and the historical-task corpus ingested
(``scripts/build_task_corpus.py --ingest``). ``--stub`` swaps in the offline
reference retrieval so the loop can be exercised without a database.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.dependencies import get_async_openai_client  # noqa: E402
from app.generation.agentic.agent_loop import (  # noqa: E402
    run_structure_agent,
    run_task_hours_recovery_agent,
)
from app.generation.agentic.agent_schemas import AgentStructure, AgentTaskHoursRun, AgentTaskRef  # noqa: E402
from app.generation.rag.agent_retrieval import default_retrieval_backend  # noqa: E402
from app.generation.rag.task_hours import distance_weighted_consensus  # noqa: E402

STUB_PATH = REPO_ROOT / "exercises" / "session-12" / "reference_retrieval.py"


def _load_stub_backend():
    """Load the student safety-net retrieval stub and adapt it to a RetrievalBackend."""
    spec = importlib.util.spec_from_file_location("s12_reference_retrieval", STUB_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load stub retrieval from {STUB_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def stub_backend(query: str, sectors: list[str] | None) -> list[dict]:
        filters = {"sectors": sectors, "component_type": None} if sectors else None
        return module.search_budgets_stub(query, filters)

    return stub_backend


def _tasks_from_structure(structure: AgentStructure) -> list[AgentTaskRef]:
    """Flatten the proposed tree into recovery refs (CLI: ground every task)."""
    return [
        AgentTaskRef(
            module=m.name,
            task=t.name,
            description=t.description,
            reason="CLI demo: derive hours for every proposed task",
        )
        for m in structure.modules
        for t in m.tasks
    ]


def _render_structure(structure: AgentStructure, trace_text: str) -> str:
    lines = [
        "=" * 78,
        "PHASE 1 — STRUCTURE (agent decomposition)",
        "=" * 78,
        trace_text,
        "",
        f"confidence: {structure.confidence}",
        "",
    ]
    for m in structure.modules:
        lines.append(f"  # {m.name}")
        for t in m.tasks:
            desc = f" — {t.description}" if t.description else ""
            lines.append(f"    - {t.name}{desc}")
    return "\n".join(lines)


def _render_hours(run: AgentTaskHoursRun) -> str:
    lines = [
        "",
        "=" * 78,
        f"PHASE 2 — HOURS RECOVERY  (iterations={run.iterations}, stopped={run.stopped_reason})",
        "=" * 78,
        run.trace.render(),
        "",
        "DERIVED HOURS",
    ]
    if not run.derivations:
        lines.append("  (the agent grounded no task)")
        return "\n".join(lines)
    total = 0
    for d in run.derivations:
        if d.has_match and d.estimated_hours is not None:
            total += d.estimated_hours
            lines.append(
                f"  - {d.module} / {d.task}: {d.estimated_hours}h  (reliability {d.reliability})"
            )
        else:
            lines.append(f"  - {d.module} / {d.task}: unresolved")
    lines.append("")
    lines.append(f"  TOTAL (grounded tasks): {total}h")
    return "\n".join(lines)


async def _main_async(args: argparse.Namespace) -> int:
    transcript_path = Path(args.transcript)
    if not transcript_path.is_file():
        print(f"ERROR: transcript not found: {transcript_path}", file=sys.stderr)
        return 1

    client = get_async_openai_client()
    if client is None:
        print(
            "ERROR: OPENAI_API_KEY is not set — the agent needs the OpenAI Responses API.",
            file=sys.stderr,
        )
        return 1

    backend = _load_stub_backend() if args.stub else default_retrieval_backend
    transcript = transcript_path.read_text(encoding="utf-8")

    print(f"transcript : {transcript_path}")
    print(
        f"model      : {args.model}   effort: {args.effort}   backend: "
        f"{'stub' if args.stub else 'retrieve() pipeline'}"
    )
    print()

    # Phase 1 — the agent proposes the structure (the transcript is the brief).
    structure, structure_trace = await run_structure_agent(
        transcript,
        client=client,
        model=args.model,
        reasoning_effort=args.effort,
    )
    rendered = _render_structure(structure, structure_trace.render())

    # Human gate (auto-approved here) → phase 2 recovery over every task.
    run = await run_task_hours_recovery_agent(
        _tasks_from_structure(structure),
        client=client,
        model=args.model,
        reasoning_effort=args.effort,
        max_iterations=args.max_iterations,
        retrieval_backend=backend,
        consensus_fn=distance_weighted_consensus,
    )
    rendered += "\n" + _render_hours(run)

    print(rendered)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
        print(f"\n(trace written to {args.out})")
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the Session 12 estimation agent.")
    parser.add_argument("transcript", help="Path to a meeting transcript .txt file.")
    parser.add_argument(
        "--model",
        default=settings.AGENT_MODEL,
        help=f"OpenAI model (default {settings.AGENT_MODEL}).",
    )
    parser.add_argument(
        "--effort",
        default=settings.AGENT_REASONING_EFFORT,
        choices=["minimal", "low", "medium", "high"],
        help="Reasoning effort for the Responses API.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=settings.AGENT_MAX_ITERATIONS,
        help="Loop safeguard: max Responses API round-trips.",
    )
    parser.add_argument(
        "--stub",
        action="store_true",
        help="Use the offline reference retrieval stub (no database).",
    )
    parser.add_argument("--out", help="Write the rendered trace + estimate to this file.")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
