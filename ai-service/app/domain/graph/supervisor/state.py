"""The supervisor flow's shared state — Session 13's state, EXTENDED.

``SupervisorState`` SUBCLASSES ``EstimationState`` rather than redeclaring it. That is
the literal reading of "estado tipado extendido desde el de la S13", and ``TypedDict``
inheritance makes LangGraph fold the parent's reducers into the child's channel set,
so ``budget_matches`` (``operator.add``) and ``errors`` (``operator.add``) arrive
already correct — along with the ``Component``/``BudgetMatch`` shapes and the field
vocabulary of the five pre-exercise nodes this flow reorganises.

The cost, stated plainly: the Session 13 *live* channels (``structure``,
``task_hours``, ``gate1_decision``, …) come along for the ride and show up empty in
``snapshot.values``. They are ``total=False`` and no Session 14 node ever writes them,
so the cost is one unused channel each — cheaper than duplicating six field
definitions plus two reducer annotations, and invisible over HTTP because the router's
response model projects only the fields it cares about.

Two NEW accumulators carry what this session is about:

* ``agent_contributions`` — the audit trail (Level 3). One row per thing an agent did:
  a model call, a tool call, or a DENIED tool call. This is the accumulator the
  exercise asks for.
* ``routing_history`` — one row per supervisor decision, with the model's own reason.
  Routing that is not in the state is routing nobody can audit.

Both use a KEYED reducer rather than ``operator.add``, for the same reason
``merge_task_hours`` does in Session 13: ``interrupt()`` RE-EXECUTES the interrupted
node on resume, so a plain concat trail would grow a duplicate row on every human
pause. Keying by identity makes a re-emitted row replace rather than append.

Note that the keyed reducer is a SAFETY NET, not a licence: the gate still calls
``interrupt()`` before it writes anything (see ``gate.py``). Relying on the reducer to
repair a bad write order would hide the bug instead of fixing it.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Optional

# Pydantic requires typing_extensions.TypedDict on Python < 3.12; LangGraph accepts it.
from typing_extensions import TypedDict

from app.domain.graph.state import EstimationState


class AgentContribution(TypedDict, total=False):
    """One auditable action: which agent, which tool, what happened.

    ``outcome`` is ``"ok"`` | ``"denied"`` | ``"error"``. ``tool`` is ``None`` for
    model-only work (the requirements extractor) and for the human's decision, so the
    trail has no holes: everything an actor did is one row.

    ``args_digest`` is a short SHA-256 of the canonical arguments. It proves identity
    (the same call twice produces the same digest) without dumping a transcript into
    the log.
    """

    step: int
    agent: str
    action: str
    tool: Optional[str]
    outcome: str
    summary: str
    args_digest: Optional[str]
    duration_ms: Optional[int]


class RoutingRecord(TypedDict, total=False):
    """One supervisor decision.

    ``source`` records WHO actually decided: ``"llm"`` when the model's choice stood,
    ``"fallback"`` when the legality guard overrode it, ``"limit"`` when the step
    budget forced a finish. Without it a trace cannot distinguish a model that routed
    well from a model that was corrected on every step.
    """

    step: int
    next_agent: str
    reason: str
    source: str
    decision_confidence: Optional[str]


def _keyed_append(
    existing: list[dict] | None,
    new: list[dict] | None,
    *,
    key: Callable[[dict], tuple],
) -> list[dict]:
    """Append-only accumulator that is IDEMPOTENT under node re-execution.

    Rows keep first-seen insertion order (an audit trail has to read chronologically);
    a repeated key merges into the existing row in place instead of appending a second
    one. That is what makes the trail survive a resume without growing phantom rows.
    """
    merged: dict[tuple, dict] = {}
    for item in list(existing or []) + list(new or []):
        item_key = key(item)
        merged[item_key] = {**merged.get(item_key, {}), **item}
    return list(merged.values())


def _contribution_key(contribution: dict) -> tuple:
    """Identity of an action: step, agent, action AND the arguments it was called with.

    ``args_digest`` is part of the key because one agent legitimately calls one tool
    several times within a single step — ``budget_searcher`` searches once per
    component. Keying on ``(step, agent, action)`` alone would make the second search
    REPLACE the first and the trail would silently lose rows. Same call re-executed on
    a resume → same digest → still idempotent; different arguments → different row.
    """
    return (
        contribution.get("step"),
        contribution.get("agent"),
        contribution.get("action"),
        contribution.get("args_digest"),
    )


def _routing_key(record: dict) -> tuple:
    return (record.get("step"),)


def append_contributions(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """Reducer for ``agent_contributions`` — keyed by ``(step, agent, action)``."""
    return _keyed_append(existing, new, key=_contribution_key)


def append_routing(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """Reducer for ``routing_history`` — keyed by ``step`` (one decision per step)."""
    return _keyed_append(existing, new, key=_routing_key)


class SupervisorState(EstimationState, total=False):
    """The state threaded through the supervisor graph.

    Inherits from Session 13: ``transcript``, ``estimation_id``, ``requirements``,
    ``components``, ``budget_matches`` (reducer), ``estimate``, ``status`` and
    ``errors`` (reducer) all come from ``EstimationState``.
    """

    # --- supervisor routing ------------------------------------------------ #
    next_agent: Optional[str]
    route_reason: Optional[str]
    # Plain last-write-wins int with exactly ONE writer (the supervisor node). It must
    # NOT get a reducer: an accumulating counter would break the step budget on resume.
    supervisor_steps: int
    routing_history: Annotated[list[RoutingRecord], append_routing]

    # --- the audit trail (Level 3) ----------------------------------------- #
    agent_contributions: Annotated[list[AgentContribution], append_contributions]

    # --- estimate_generator ------------------------------------------------ #
    # Deterministic per-component hours from the consensus tool, BEFORE the model
    # consolidates them. Keeping them lets the trace show what was grounded and what
    # the model added on top.
    component_anchors: list[dict]

    # --- coherence_validator: FACTS the gate reads (never the verdict) ------ #
    validation: Optional[dict]
    confidence: Optional[float]
    out_of_range: Optional[bool]
    grounded_components: Optional[int]

    # --- human_review_gate -------------------------------------------------- #
    needs_human_review: Optional[bool]
    review_reasons: list[str]
    human_decision: Optional[dict]

    # --- Session 14 (LIVE): competition ------------------------------------- #
    # The two competing proposals, their arithmetic divergence and the synthesized
    # range. All written once by the competitive estimate node (plain last-write-wins);
    # ``divergence`` is a FACT the coherence validator folds into the confidence signal.
    proposals: Optional[list[dict]]
    divergence: Optional[dict]
    synthesis: Optional[dict]

    # --- Session 14 (LIVE): sandboxing / persistence ------------------------ #
    # ``persist_requested`` is set by the validator when persistence is enabled; it is a
    # gate trigger (an irreversible write must be authorised by a person). ``saved`` is
    # the guarded write's outcome envelope.
    persist_requested: Optional[bool]
    saved: Optional[dict]


def privilege_violations(state: dict[str, Any]) -> list[dict]:
    """Every denied action in the trail.

    A derived read model, deliberately NOT its own channel: a privilege violation *is*
    an agent action, and keeping it in the one ordered trail is what makes "reconstruct
    the run from the log" true rather than aspirational.
    """
    return [
        contribution
        for contribution in (state.get("agent_contributions") or [])
        if contribution.get("outcome") == "denied"
    ]
