"""Human-in-the-loop: pause when the estimate is not trustworthy enough to ship.

Session 13's gates pause UNCONDITIONALLY at fixed points in the flow. This one pauses
on a SIGNAL: the graph runs unattended when the numbers are well grounded, and stops
for a person exactly when they are not. That difference is the whole point — a human
gate that always fires is a form, not a control.

The three trigger conditions are the ones the exercise names:

1. confidence below the configured threshold,
2. the estimate falls outside the range its historical references imply,
3. the transcript has essentially no precedent in the budget corpus.

The split of responsibility matters and is deliberate: ``coherence_validator`` writes
FACTS (``confidence``, ``out_of_range``, ``grounded_components``); this module owns the
VERDICT. That is what lets the threshold move via configuration — or a second trigger
be added — without touching the validator.
"""

from __future__ import annotations

import logfire
import structlog
from langgraph.types import interrupt

from app.config import Settings, get_settings
from app.domain.graph.supervisor.state import SupervisorState

log = structlog.get_logger()

# One working day = 8 engineer-hours (same convention as the rest of the graph).
HOURS_PER_DAY = 8.0


def review_reasons(state: SupervisorState, settings: Settings | None = None) -> list[str]:
    """The trigger conditions that currently hold. Empty list == ship it.

    A PURE function of state: no I/O, no clock, no random draw. That is not tidiness,
    it is a correctness requirement — ``interrupt()`` re-executes this node on resume,
    so the pause/no-pause branch MUST be taken the same way the second time. A trigger
    that read ``datetime.now()`` could resume down the other branch and assign the
    human's answer to the wrong pause.

    Keeping it out of the node body also makes each condition unit-testable on its own.
    """
    settings = settings or get_settings()
    reasons: list[str] = []

    confidence = state.get("confidence")
    if confidence is not None and confidence < settings.SUPERVISOR_CONFIDENCE_THRESHOLD:
        reasons.append(
            f"confidence {confidence:.2f} is below the "
            f"{settings.SUPERVISOR_CONFIDENCE_THRESHOLD:.2f} threshold"
        )

    if state.get("out_of_range"):
        reasons.append(
            "at least one component falls outside the plausible range implied by its "
            "historical references"
        )

    total = len(state.get("components") or [])
    grounded = state.get("grounded_components") or 0
    if total and (grounded / total) < settings.SUPERVISOR_MIN_GROUNDED_RATIO:
        reasons.append(
            f"only {grounded}/{total} components have any precedent in the historical budgets"
        )

    # Trigger 4 (S14 live sandboxing): an irreversible write is queued. Routing it here is
    # deliberate — the same pause that reviews the estimate authorises the no-undo save.
    if state.get("persist_requested"):
        reasons.append(
            "an irreversible save_estimate is queued; the human pause authorises the write"
        )

    return reasons


def needs_human_review(state: SupervisorState, settings: Settings | None = None) -> bool:
    """Whether this estimate must stop for a person."""
    return bool(review_reasons(state, settings))


def _apply_decision(state: SupervisorState, decision: dict) -> tuple[dict, str]:
    """Fold the human's answer into the estimate. Returns ``(estimate, status)``."""
    estimate = dict(state.get("estimate") or {})
    action = (decision or {}).get("decision") or (decision or {}).get("action") or "approve"

    if action == "reject":
        # The estimate is kept verbatim: a rejected estimate is evidence, and
        # overwriting it would destroy what the reviewer was looking at.
        return estimate, "rejected"

    if action == "adjust":
        overrides = (decision or {}).get("estimate_overrides") or {}
        estimate = {**estimate, **overrides}
        # The merge is shallow, so if the reviewer edited per-component days the
        # headline total would otherwise contradict them. Rederive it.
        components = estimate.get("components") or []
        if components:
            estimate["total_engineer_days"] = sum(
                int(c.get("engineer_days") or 0) for c in components
            )

    return estimate, "validated"


async def human_review_gate(state: SupervisorState) -> dict:
    """Pause for a person when the estimate trips a trigger; otherwise fall through.

    Discipline, identical to ``agents/gates.py``: ``interrupt()`` is called FIRST,
    before any state write and OUTSIDE the logfire span, because a resume RE-EXECUTES
    this node from the top. Anything written above the interrupt would run twice — and
    for the ``agent_contributions`` accumulator that would mean a duplicated audit row.
    The keyed reducer would silently repair it, which is worse than the bug: get the
    ordering right and let the reducer be a safety net, not the mechanism.

    Resume payload::

        {"decision": "approve" | "adjust" | "reject",
         "estimate_overrides": {...},   # only meaningful for "adjust"
         "note": "..."}
    """
    reasons = review_reasons(state)  # pure read — safe above the interrupt

    if not reasons:
        with logfire.span("gate: human_review (auto-approved)"):
            log.info(
                "human_review_gate_skipped",
                confidence=state.get("confidence"),
                status=state.get("status"),
            )
            return {"needs_human_review": False, "review_reasons": []}

    settings = get_settings()
    decision = interrupt(
        {
            "gate": "low_confidence_review",
            "estimation_id": state.get("estimation_id"),
            "reasons": reasons,
            "confidence": state.get("confidence"),
            "threshold": settings.SUPERVISOR_CONFIDENCE_THRESHOLD,
            "estimate": state.get("estimate"),
            "validation": state.get("validation"),
            "routing_history": state.get("routing_history") or [],
        }
    )

    with logfire.span("gate: human_review"):
        decision = decision or {}
        estimate, status = _apply_decision(state, decision)
        action = decision.get("decision") or decision.get("action") or "approve"
        log.info(
            "human_review_gate_resumed",
            action=action,
            status=status,
            reasons=len(reasons),
        )
        return {
            "estimate": estimate,
            "status": status,
            "human_decision": decision,
            "needs_human_review": True,
            "review_reasons": reasons,
            "agent_contributions": [
                {
                    "step": int(state.get("supervisor_steps") or 0),
                    "agent": "human",
                    "action": "review_decision",
                    "tool": None,
                    "outcome": "ok",
                    "summary": f"human {action}: {decision.get('note') or '—'}",
                    "args_digest": None,
                    "duration_ms": None,
                }
            ],
        }
