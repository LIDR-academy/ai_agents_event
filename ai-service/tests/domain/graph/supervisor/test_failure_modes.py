"""Pins the three Session 14 failure-mode reproductions: symptom AND fix.

The modules live under ``exercises/session-14/failure_modes/`` (teaching artifacts, loaded
the same way the demo runner loads the student stub). Each test asserts the broken
behaviour and the fixed behaviour, so the "before/after" shown live is reproducible.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from langgraph.errors import InvalidUpdateError

_FM_DIR = Path(__file__).resolve().parents[4] / "exercises" / "session-14" / "failure_modes"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"fm_{name}", _FM_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so ``get_type_hints`` (used by LangGraph on the state
    # TypedDicts under ``from __future__ import annotations``) can resolve module globals.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# --------------------------------------------------------------------------- #
# #1 — routing that does not converge                                         #
# --------------------------------------------------------------------------- #
def test_broken_router_ping_pongs_until_the_step_budget_cuts_it():
    routing = _load("routing_no_converge")
    pingpong = ["requirements_extractor", "budget_searcher"]

    history = routing.run_router(pingpong, guard=False)

    # The loop is stopped only by the budget, and the trail proves it.
    assert history[-1]["source"] == "limit"
    dispatched = [row["next_agent"] for row in history]
    assert dispatched.count("requirements_extractor") > 1  # the same agent, again and again


def test_fixed_router_converges_without_hitting_the_budget():
    routing = _load("routing_no_converge")
    pingpong = ["requirements_extractor", "budget_searcher"]

    history = routing.run_router(pingpong, guard=True)

    assert history[-1]["source"] != "limit"  # it finished on its own merits
    dispatched = [row["next_agent"] for row in history if row["next_agent"] != "finish"]
    assert len(dispatched) == len(set(dispatched))  # no agent ran twice


# --------------------------------------------------------------------------- #
# #2 — an accumulator channel with no reducer loses writes                    #
# --------------------------------------------------------------------------- #
def test_plain_channel_rejects_the_concurrent_write():
    clobber = _load("state_clobber")
    graph = clobber.build_clobber_graph(clobber.PlainState)

    # Two parallel writes into a reducer-less channel: LangGraph refuses rather than
    # silently drop one.
    with pytest.raises(InvalidUpdateError):
        graph.invoke({"contributions": []})


def test_reducer_channel_keeps_both_writes():
    clobber = _load("state_clobber")
    graph = clobber.build_clobber_graph(clobber.AccumulatingState)

    result = graph.invoke({"contributions": []})

    agents = sorted(row["agent"] for row in result["contributions"])
    assert agents == ["a", "b"]  # nothing lost


# --------------------------------------------------------------------------- #
# #3 — interrupt() that never resumes (thread_id mismatch)                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mismatched_thread_id_leaves_the_run_paused():
    mod = _load("interrupt_no_resume")
    graph = mod.build_gate_graph()

    report = await mod.start_and_resume(
        graph, "est-1", start_thread="thread-A", resume_thread="thread-B"
    )

    # The resume hit a different thread; the original run never moved.
    assert report["start_paused"] is True
    assert report["start_done"] is False


@pytest.mark.asyncio
async def test_matching_thread_id_resumes_the_same_run():
    mod = _load("interrupt_no_resume")
    graph = mod.build_gate_graph()

    report = await mod.start_and_resume(
        graph, "est-1", start_thread="s14:est-1", resume_thread="s14:est-1", decision="approve"
    )

    assert report["start_paused"] is False
    assert report["start_done"] is True
    assert report["start_decision"] == "approve"
