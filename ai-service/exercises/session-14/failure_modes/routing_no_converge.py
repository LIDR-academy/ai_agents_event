"""Failure mode #1 — a supervisor that never converges (routing loop).

SYMPTOM (live): the run bounces requirements_extractor → budget_searcher →
requirements_extractor → budget_searcher … and only stops when the step budget forces a
``finish``. ``routing_history`` shows the ping-pong and ends with a ``source == "limit"``
row.

CAUSE: cyclic return edges (every agent hands control back to the supervisor) plus a
router that may re-choose an agent that already ran. The legality guard is what forbids a
re-visit; drop it and the loop is inevitable — a scripted OR an LLM router will happily
oscillate.

FIX (str_replace on screen): flip ``guard`` on — restore the ``and not _already_ran(...)``
clause in ``_is_legal``. One clause turns the ping-pong into a convergent flow.

This is a standalone reproduction of the exact brakes in
``app/domain/graph/supervisor/supervisor.py`` (``_already_ran`` / ``_is_legal`` /
``_fallback_next`` / the step budget), boiled down so the loop is visible in a few lines.
"""

from __future__ import annotations

# Same default as SUPERVISOR_MAX_STEPS: the hard ceiling that makes an LLM router safe in
# a graph with cyclic return edges.
MAX_ROUTING_STEPS = 8

# A two-agent dependency ladder is enough to loop.
_ORDER = ["requirements_extractor", "budget_searcher"]


def _already_ran(agent: str, history: list[dict]) -> bool:
    """Whether ``agent`` was dispatched earlier — read from the routing history, not from
    whether its output channel is populated (an empty-but-legitimate result must not read
    as 'not done')."""
    return any(record["next_agent"] == agent for record in history)


def _inputs_ready(agent: str, history: list[dict]) -> bool:
    if agent == "requirements_extractor":
        return True
    if agent == "budget_searcher":
        return _already_ran("requirements_extractor", history)
    return False


def _is_legal(target: str, history: list[dict], *, guard: bool) -> bool:
    """Whether routing to ``target`` right now is coherent.

    The ``guard`` flag is the bug switch. ``guard=False`` (the default, BROKEN) checks
    only that the inputs exist — so an agent that already ran is still 'legal' and the
    router can pick it forever. ``guard=True`` (the FIX) also refuses a re-visit.
    """
    if target == "finish":
        return True
    if target not in _ORDER:
        return False
    if guard:
        # --- FIX: an agent that already acted is never legal again -------- #
        return _inputs_ready(target, history) and not _already_ran(target, history)
    # --- BROKEN: missing the "not already ran" clause --------------------- #
    return _inputs_ready(target, history)


def _fallback_next(history: list[dict], *, guard: bool) -> str:
    """Deterministic dependency ladder: the first agent that can still act."""
    for agent in _ORDER:
        if _is_legal(agent, history, guard=guard):
            return agent
    return "finish"


def run_router(
    route_script: list[str], *, guard: bool = False, max_steps: int = MAX_ROUTING_STEPS
) -> list[dict]:
    """Drive a scripted router through the brakes and return its ``routing_history``.

    ``route_script`` is what the 'model' asks for at each step. The ping-pong scenario
    passes ``["requirements_extractor", "budget_searcher"]`` repeating: with ``guard`` off
    the router keeps re-choosing them and only the step budget saves the run; with
    ``guard`` on the second visit is illegal, the fallback finds nothing legal left, and
    the run finishes cleanly.
    """
    history: list[dict] = []
    for step in range(max_steps + 1):
        # --- brake 1: the step budget -------------------------------------- #
        if step >= max_steps:
            history.append(
                {
                    "step": step,
                    "next_agent": "finish",
                    "source": "limit",
                    "reason": f"step budget of {max_steps} exhausted",
                }
            )
            break

        proposed = route_script[step % len(route_script)] if route_script else "finish"
        # --- brake 2: the legality guard ----------------------------------- #
        if _is_legal(proposed, history, guard=guard):
            target, source = proposed, "llm"
        else:
            target, source = _fallback_next(history, guard=guard), "fallback"

        history.append(
            {"step": step, "next_agent": target, "source": source, "reason": f"routed to {target}"}
        )
        if target == "finish":
            break
    return history
