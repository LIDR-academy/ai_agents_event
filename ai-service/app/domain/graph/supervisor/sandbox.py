"""Agent-level sandboxing (Session 14 LIVE): the three containment layers for WRITES.

The reference solution already enforces MINIMUM PRIVILEGE over read tools
(``privilege.AGENT_PRIVILEGES`` + ``guarded_dispatch``). This module extends that to the
one place it really bites — an agent that can WRITE — with three deterministic layers,
all in plain Python. There is NO process isolation, OS sandbox or secret management here:
that is Session 15. "Sandboxing" at this layer means privilege + argument validation +
audit, which is what actually stops an agent doing the wrong irreversible thing.

Layer 1 — GRANTS with a RISK dimension. ``AGENT_TOOL_GRANTS`` extends the privilege
    table with the write-capable ``persistence_agent``, and ``TOOL_RISK`` classifies
    every tool READ / WRITE / IRREVERSIBLE. ``verify_tool_grants()`` runs at graph-build
    time and FAILS THE STARTUP if an agent is granted a tool with no declared risk or no
    implementation — a misconfiguration can never reach runtime.

Layer 2 — ARGUMENT VALIDATION before execution. ``guard_action`` is pure, deterministic
    code: it checks the allowlist, validates the arguments, and — the load-bearing check
    for a multi-tenant system — verifies the action's ``estimation_id`` matches the run
    in progress, so one run can never write another run's estimate. An IRREVERSIBLE tool
    additionally requires a recorded human approval; without it the write is refused and
    the flow is expected to route through the human gate (see ``gate.review_reasons``).

Layer 3 — AUDIT of every intent WITH EFFECTS, including the denied ones.
    ``execute_guarded`` logs a structlog event for allowed, denied AND deferred writes,
    with the ``estimation_id`` and a REDACTED argument preview (the full SHA-256 digest
    is always logged, so a call's identity is provable without dumping sensitive data).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable

import structlog

from app.config import get_settings

# Compose over the existing privilege layer — do not duplicate it. ``_digest`` is reused
# so a write's audit fingerprint has the exact same shape as a read's.
from app.domain.graph.supervisor.privilege import AGENT_PRIVILEGES, _digest

log = structlog.get_logger()

# The one write tool this session introduces. Every other tool stays read/pure.
SAVE_ESTIMATE_TOOL = "save_estimate"


class ToolRisk(enum.StrEnum):
    """How much damage a tool can do if the model calls it wrongly."""

    READ = "read"  # retrieval / validation — no side effects
    WRITE = "write"  # mutates state that can be corrected
    IRREVERSIBLE = "irreversible"  # commits something with no cheap undo


# Every tool the agents may be granted, classified by risk. ``verify_tool_grants``
# rejects a grant whose tool is missing here, so this table is the single source of truth
# for "what tools exist and how dangerous each is".
TOOL_RISK: dict[str, ToolRisk] = {
    "search_budgets": ToolRisk.READ,
    "derive_task_hours": ToolRisk.READ,
    "validate_estimate": ToolRisk.READ,
    SAVE_ESTIMATE_TOOL: ToolRisk.IRREVERSIBLE,
}


# The grant table: the reference privilege table PLUS the write-capable agent. Keeping
# the write in its OWN agent is the point — the four read agents cannot save even by
# accident, because the capability is not in their row at all.
AGENT_TOOL_GRANTS: dict[str, frozenset[str]] = {
    **AGENT_PRIVILEGES,
    "persistence_agent": frozenset({SAVE_ESTIMATE_TOOL}),
}


class GrantVerificationError(RuntimeError):
    """A tool grant references a tool with no declared risk or no implementation."""


def verify_tool_grants(known_tools: set[str] | None = None) -> None:
    """Fail fast if the grant table is inconsistent. Called at graph-build time.

    A grant that names a tool the system does not know about — a typo, a renamed tool, a
    capability nobody implemented — is exactly the kind of latent hole that only shows up
    when an agent finally reaches for it in production. Raising here turns it into a
    startup crash instead.
    """
    known = known_tools if known_tools is not None else set(TOOL_RISK)
    for agent, tools in AGENT_TOOL_GRANTS.items():
        for tool in tools:
            if tool not in TOOL_RISK:
                raise GrantVerificationError(
                    f"agent {agent!r} is granted tool {tool!r}, which has no declared "
                    f"ToolRisk — every granted tool must be classified in TOOL_RISK"
                )
            if tool not in known:
                raise GrantVerificationError(
                    f"agent {agent!r} is granted tool {tool!r}, which is not a known "
                    f"tool ({sorted(known)})"
                )


@dataclass(frozen=True)
class ActionRequest:
    """A request to perform a tool action, carrying the run it belongs to."""

    agent: str
    tool: str
    args: dict[str, Any]
    estimation_id: str | None
    step: int


@dataclass(frozen=True)
class GuardDecision:
    """The guard's verdict. ``allowed`` gates execution; ``requires_human_approval`` is
    set for an irreversible action that has not been authorised by a person yet."""

    allowed: bool
    requires_human_approval: bool = False
    reason: str = ""
    risk: ToolRisk | None = None
    redacted_args: dict[str, Any] = field(default_factory=dict)


# Argument keys that must never land in the audit preview verbatim.
_SENSITIVE_KEYS = {"transcript", "note", "content", "body", "reasoning"}


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive values for the audit preview. The full digest is logged separately,
    so identity is preserved without echoing free text into the log."""
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        if key in _SENSITIVE_KEYS:
            redacted[key] = "«redacted»"
        elif isinstance(value, str) and len(value) > 80:
            redacted[key] = value[:77] + "…"
        else:
            redacted[key] = value
    return redacted


def _human_approved(state: dict[str, Any]) -> bool:
    """Whether a person approved this run at the human gate."""
    decision = state.get("human_decision") or {}
    action = decision.get("decision") or decision.get("action")
    return action == "approve"


def guard_action(req: ActionRequest, state: dict[str, Any]) -> GuardDecision:
    """Decide whether ``req`` may execute — privilege + arguments + tenancy, all pure.

    The checks, in order:

    1. **Privilege** — the tool must be in the agent's grant. A denied call never reaches
       the tool.
    2. **Arguments** — the payload must be a well-formed dict; a write must carry the
       estimate it intends to save.
    3. **Tenancy** — the action's ``estimation_id`` must match the run in progress AND
       any id embedded in the arguments. This is what stops one run writing another's
       estimate; it is cheap and it is the check people forget.
    4. **Irreversibility** — an ``IRREVERSIBLE`` tool needs a recorded human approval.
       Without it the action is not allowed to execute; the flow routes it to the human
       gate, where the pause doubles as authorisation.
    """
    redacted = _redact(req.args if isinstance(req.args, dict) else {})
    risk = TOOL_RISK.get(req.tool)

    granted = AGENT_TOOL_GRANTS.get(req.agent, frozenset())
    if req.tool not in granted:
        return GuardDecision(
            allowed=False,
            reason=f"agent {req.agent!r} is not granted tool {req.tool!r} "
            f"(granted: {sorted(granted) or 'none'})",
            risk=risk,
            redacted_args=redacted,
        )

    if not isinstance(req.args, dict):
        return GuardDecision(
            allowed=False, reason="arguments must be an object", risk=risk, redacted_args=redacted
        )

    # Tenancy: the action must belong to the run in progress.
    run_id = state.get("estimation_id")
    if req.estimation_id != run_id:
        return GuardDecision(
            allowed=False,
            reason=f"action estimation_id {req.estimation_id!r} does not match the "
            f"current run {run_id!r}",
            risk=risk,
            redacted_args=redacted,
        )
    args_id = req.args.get("estimation_id")
    if args_id is not None and args_id != run_id:
        return GuardDecision(
            allowed=False,
            reason=f"argument estimation_id {args_id!r} does not match the current run {run_id!r}",
            risk=risk,
            redacted_args=redacted,
        )

    if req.tool == SAVE_ESTIMATE_TOOL and not req.args.get("estimate"):
        return GuardDecision(
            allowed=False,
            reason="save_estimate requires a non-empty 'estimate' payload",
            risk=risk,
            redacted_args=redacted,
        )

    if risk == ToolRisk.IRREVERSIBLE and not _human_approved(state):
        return GuardDecision(
            allowed=True,
            requires_human_approval=True,
            reason="irreversible action requires a human approval; route through the gate",
            risk=risk,
            redacted_args=redacted,
        )

    return GuardDecision(allowed=True, reason="ok", risk=risk, redacted_args=redacted)


# A save sink is injectable so the tool has NO real side effect in tests or offline runs.
# This session does not touch a database (that is Session 15); the default sink records
# the intent in-process and logs it.
SaveSink = Callable[[str | None, dict[str, Any]], dict[str, Any]]

# Visible for tests / the demo runner to assert what "would have been persisted".
PERSISTED: dict[str, dict[str, Any]] = {}


def _default_sink(estimation_id: str | None, estimate: dict[str, Any]) -> dict[str, Any]:
    """The default, side-effect-free sink: record in-process, log the intent."""
    record = {"estimation_id": estimation_id, "estimate": estimate}
    PERSISTED[estimation_id or "?"] = record
    log.info(
        "persistence_would_write",
        estimation_id=estimation_id,
        total_engineer_days=(estimate or {}).get("total_engineer_days"),
    )
    return {"ok": True, "stored": True}


async def execute_guarded(
    req: ActionRequest,
    state: dict[str, Any],
    *,
    sink: SaveSink | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Guard, execute (if cleared), and audit — returns ``(envelope, contribution)``.

    Every intent with effects is logged, including the denied and the deferred ones, so
    the audit trail is complete: a run's writes are reconstructible from the log alone.
    The contribution is RETURNED (not written) so the calling agent stays a pure
    ``state -> partial update`` function, exactly like ``guarded_dispatch``.
    """
    settings = get_settings()
    started = perf_counter()
    digest = _digest(req.args if isinstance(req.args, dict) else {})
    decision = guard_action(req, state)
    preview = str(decision.redacted_args)[: settings.SUPERVISOR_AUDIT_ARGS_PREVIEW_CHARS]

    def _contribution(outcome: str, summary: str) -> dict[str, Any]:
        return {
            "step": req.step,
            "agent": req.agent,
            "action": f"tool:{req.tool}",
            "tool": req.tool,
            "outcome": outcome,
            "summary": summary[:200],
            "args_digest": digest,
            "duration_ms": int((perf_counter() - started) * 1000),
        }

    # --- denied: privilege / arguments / tenancy --------------------------- #
    if not decision.allowed:
        log.error(
            "agent_privilege_denied",
            estimation_id=req.estimation_id,
            step=req.step,
            agent=req.agent,
            tool=req.tool,
            risk=str(decision.risk),
            args_digest=digest,
            args_preview=preview,
            reason=decision.reason,
        )
        return (
            {"ok": False, "error": "denied", "summary": decision.reason},
            _contribution("denied", decision.reason),
        )

    # --- deferred: irreversible, not yet authorised by a human ------------- #
    if decision.requires_human_approval:
        log.warning(
            "agent_action_deferred",
            estimation_id=req.estimation_id,
            step=req.step,
            agent=req.agent,
            tool=req.tool,
            risk=str(decision.risk),
            args_digest=digest,
            args_preview=preview,
            reason=decision.reason,
        )
        return (
            {"ok": False, "error": "awaiting_human_approval", "summary": decision.reason},
            _contribution("deferred", decision.reason),
        )

    # --- cleared: execute the write ---------------------------------------- #
    try:
        run_sink = sink or _default_sink
        result = run_sink(req.estimation_id, req.args.get("estimate") or {})
        outcome, summary = "ok", "estimate persisted (guarded, human-authorised)"
    except Exception as exc:  # noqa: BLE001 — a failed write must not kill the graph.
        result = {"ok": False, "error": type(exc).__name__, "summary": str(exc)[:200]}
        outcome, summary = "error", str(exc)[:200]

    log.info(
        "agent_action",
        estimation_id=req.estimation_id,
        step=req.step,
        agent=req.agent,
        tool=req.tool,
        action=f"tool:{req.tool}",
        outcome=outcome,
        risk=str(decision.risk),
        args_digest=digest,
        args_preview=preview,
        result_summary=summary,
    )
    return result, _contribution(outcome, summary)
