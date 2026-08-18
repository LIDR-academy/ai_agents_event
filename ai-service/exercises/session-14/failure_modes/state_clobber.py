"""Failure mode #2 — an accumulator channel with no reducer loses writes.

SYMPTOM (live): two agents run in the same superstep (a fan-out) and both append to the
audit trail. Without a reducer, LangGraph refuses the concurrent write with
``InvalidUpdateError: Can receive only one value per step`` — or, in a sequential re-entry
(a resumed interrupt), the second write silently REPLACES the first and a row vanishes
from the trail.

CAUSE: the channel was declared as a plain ``list``. A LangGraph channel with no reducer
is last-write-wins; an accumulator MUST declare how two writes combine.

FIX (str_replace on screen): annotate the channel with a reducer —
``Annotated[list[dict], operator.add]`` — exactly as ``agent_contributions`` and
``routing_history`` do in the real state.

Both graphs below fan two nodes out from START into the same channel; only the annotation
differs.
"""

from __future__ import annotations

import operator
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class PlainState(TypedDict, total=False):
    # --- BROKEN: a plain list channel — no reducer, so parallel writes clash --- #
    contributions: list[dict]


class AccumulatingState(TypedDict, total=False):
    # --- FIX: a reducer tells LangGraph how to COMBINE two writes ------------- #
    contributions: Annotated[list[dict], operator.add]


def _node_a(_state: dict) -> dict:
    return {"contributions": [{"agent": "a", "action": "did work"}]}


def _node_b(_state: dict) -> dict:
    return {"contributions": [{"agent": "b", "action": "did work"}]}


def build_clobber_graph(state_cls):
    """Fan ``_node_a`` and ``_node_b`` out from START into ``contributions``, join at END.

    Pass ``PlainState`` to reproduce the bug, ``AccumulatingState`` to see it fixed.
    """
    builder = StateGraph(state_cls)
    builder.add_node("a", _node_a)
    builder.add_node("b", _node_b)
    builder.add_edge(START, "a")
    builder.add_edge(START, "b")
    builder.add_edge("a", END)
    builder.add_edge("b", END)
    return builder.compile()
