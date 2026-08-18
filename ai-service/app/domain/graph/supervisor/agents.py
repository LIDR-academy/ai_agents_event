"""The four specialised agents — pure ``state -> partial update`` functions.

Each one is a reorganisation of the Session 13 pre-exercise nodes (still in
``app/domain/graph/nodes.py``), not a rewrite: the prompts, the response models and
the deterministic guardrails are imported from there. What changed is the SHAPE —
five nodes wired in a fixed line become four agents that the supervisor dispatches,
each holding exactly the tools it needs and nothing more:

===========================  ===========================================
agent                        tools
===========================  ===========================================
``requirements_extractor``   (none — the model only)
``budget_searcher``          ``search_budgets``
``estimate_generator``       ``derive_task_hours`` (the "calculate" tool)
``coherence_validator``      ``validate_estimate``
===========================  ===========================================

Every tool call goes through ``guarded_dispatch``, never through ``dispatch_tool``
directly — that is what makes the privilege table load-bearing rather than
documentation. Each agent returns the contributions it collected in
``agent_contributions``, so the audit trail is assembled by the reducer rather than by
a side effect, and the agents stay pure.

``make_retrieval_backend`` and ``distance_weighted_consensus`` are imported at MODULE
level on purpose: that is the monkeypatch seam the network-free tests use, matching
the convention in ``agents/hours.py``.
"""

from __future__ import annotations

import asyncio
from time import perf_counter

import logfire
import structlog

from app.config import get_settings
from app.domain.graph.nodes import (
    HOURS_PER_DAY,
    _GENERATE_SYSTEM_PROMPT,
    _CLASSIFY_SYSTEM_PROMPT,
    _EXTRACT_SYSTEM_PROMPT,
    _max_tokens_for,
    _norm,
    _reasoning_effort_for,
    _references_for,
    _validate,
)
from app.domain.graph.schemas import (
    ComponentClassification,
    ConsolidatedEstimate,
    RequirementsExtraction,
)
from app.domain.graph.state import BudgetMatch, Component
from app.domain.graph.supervisor.competition import COMPETITION_GRAPH, compute_divergence
from app.domain.graph.supervisor.privilege import (
    CALCULATE_TOOL,
    guarded_dispatch,
    record_model_action,
)
from app.domain.graph.supervisor.sandbox import ActionRequest, execute_guarded
from app.domain.graph.supervisor.state import SupervisorState
from app.generation.rag.agent_retrieval import make_retrieval_backend
from app.generation.rag.task_hours import distance_weighted_consensus

log = structlog.get_logger()


def _step_of(state: SupervisorState) -> int:
    """The supervisor step that dispatched this agent (the audit trail's x-axis)."""
    return int(state.get("supervisor_steps") or 0)


# --------------------------------------------------------------------------- #
# requirements_extractor — NO business tools                                  #
# --------------------------------------------------------------------------- #
async def requirements_extractor(state: SupervisorState) -> dict:
    """Transcript → requirements → components. TWO structured LLM calls, ZERO tools.

    Absorbs the pre-exercise ``extract_requirements`` and ``classify_components``:
    both are pure reasoning over text with no retrieval, so splitting them across two
    agents would buy coordination overhead and nothing else.

    Minimum privilege is structural here, not a promise: this function never imports
    ``guarded_dispatch``'s tool path, and ``AGENT_PRIVILEGES["requirements_extractor"]``
    is the empty set — any tool it tried would be denied before reaching the dispatcher.
    """
    with logfire.span("agent: requirements_extractor"):
        settings = get_settings()
        from app.dependencies import get_llm_wrapper

        wrapper = get_llm_wrapper()
        step = _step_of(state)
        estimation_id = state.get("estimation_id")
        model = settings.GRAPH_EXTRACTION_MODEL

        started = perf_counter()
        extraction, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_EXTRACT_SYSTEM_PROMPT,
            user_message=state["transcript"],
            response_model=RequirementsExtraction,
            model_override=model,
        )
        requirements = [r.strip() for r in extraction.requirements if r.strip()]
        contribution_extract = record_model_action(
            "requirements_extractor",
            "extract_requirements",
            step=step,
            estimation_id=estimation_id,
            model=model,
            summary=f"{len(requirements)} requirements extracted from the transcript",
            duration_ms=int((perf_counter() - started) * 1000),
        )

        started = perf_counter()
        classification, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_CLASSIFY_SYSTEM_PROMPT,
            user_message="Requirements:\n" + "\n".join(f"- {r}" for r in requirements),
            response_model=ComponentClassification,
            model_override=model,
        )
        components: list[Component] = [
            {"name": c.name.strip(), "category": c.category.strip()}
            for c in classification.components
            if c.name.strip()
        ]
        contribution_classify = record_model_action(
            "requirements_extractor",
            "classify_components",
            step=step,
            estimation_id=estimation_id,
            model=model,
            summary=f"{len(components)} components: "
            + ", ".join(c["name"] for c in components[:5]),
            duration_ms=int((perf_counter() - started) * 1000),
        )

        log.info(
            "supervisor_agent_requirements_extractor",
            requirements=len(requirements),
            components=len(components),
        )
        return {
            "requirements": requirements,
            "components": components,
            "agent_contributions": [contribution_extract, contribution_classify],
        }


# --------------------------------------------------------------------------- #
# budget_searcher — search_budgets only                                       #
# --------------------------------------------------------------------------- #
async def budget_searcher(state: SupervisorState) -> dict:
    """For each component, retrieve historical reference budgets.

    Port of the pre-exercise ``search_budgets``, except retrieval now goes through the
    guarded dispatcher instead of calling the backend directly — so each search is
    privilege-checked, argument-validated (``SearchBudgetsArgs``) and audited.

    A per-component failure stays SOFT: it appends to ``errors`` and the loop carries
    on. One unreachable component must not lose the other components' references.
    """
    with logfire.span("agent: budget_searcher"):
        settings = get_settings()
        step = _step_of(state)
        estimation_id = state.get("estimation_id")
        backend = make_retrieval_backend(
            settings.AGENT_SEARCH_TOP_K, settings.AGENT_SEARCH_DISTANCE_THRESHOLD
        )

        matches: list[BudgetMatch] = []
        contributions: list[dict] = []
        errors: list[str] = []

        for component in state.get("components") or []:
            result, contribution = await guarded_dispatch(
                "budget_searcher",
                "search_budgets",
                {
                    "query": f"{component['name']} ({component['category']})",
                    "filters": {
                        "sectors": None,
                        "component_type": component["category"],
                    },
                },
                step=step,
                estimation_id=estimation_id,
                backend=backend,
            )
            contributions.append(contribution)

            if not result.get("ok", True) and result.get("error"):
                errors.append(
                    f"budget search failed for {component['name']!r}: {result.get('summary')}"
                )
                continue

            for item in result.get("items") or []:
                hours = item.get("estimated_hours")
                if hours is None:
                    continue
                matches.append(
                    {
                        "component": component["name"],
                        "reference_budget_id": item.get("budget_id"),
                        "amount": float(hours),
                        "distance": float(item.get("distance") or 0.0),
                    }
                )

        log.info(
            "supervisor_agent_budget_searcher",
            components=len(state.get("components") or []),
            matches=len(matches),
        )
        update: dict = {"budget_matches": matches, "agent_contributions": contributions}
        if errors:
            update["errors"] = errors
        return update


# --------------------------------------------------------------------------- #
# estimate_generator — the "calculate" tool + consolidation                   #
# --------------------------------------------------------------------------- #
def _reference_rows_for(component: str, matches: list[BudgetMatch]) -> list[BudgetMatch]:
    """Every match belonging to ``component`` (name-normalised, as the nodes do)."""
    target = _norm(component)
    return [m for m in matches if _norm(m["component"]) == target]


async def estimate_generator(state: SupervisorState) -> dict:
    """Anchor each component deterministically, then consolidate with the model.

    Two phases, which is the honest reading of "the calculate tool":

    1. **Arithmetic (the tool).** ``derive_task_hours`` runs the same distance-weighted
       consensus the Session 10 per-task path uses, over the analogs ``budget_searcher``
       gathered. No LLM — reproducible, and it is what makes the estimate *grounded*
       rather than model-invented.
    2. **Consolidation (the model).** The anchors go into the prompt alongside the raw
       references, so the model rounds and reconciles rather than picking numbers.
    """
    with logfire.span("agent: estimate_generator"):
        settings = get_settings()
        from app.dependencies import get_llm_wrapper

        wrapper = get_llm_wrapper()
        step = _step_of(state)
        estimation_id = state.get("estimation_id")
        components = state.get("components") or []
        matches = state.get("budget_matches") or []

        # --- phase 1: deterministic anchoring, under privilege ------------- #
        anchors: list[dict] = []
        contributions: list[dict] = []
        for component in components:
            rows = _reference_rows_for(component["name"], matches)
            if not rows:
                anchors.append({"name": component["name"], "has_match": False})
                continue
            result, contribution = await guarded_dispatch(
                "estimate_generator",
                CALCULATE_TOOL,
                {
                    "module": component["category"],
                    "task": component["name"],
                    "neighbors": [
                        {
                            "estimated_hours": int(row["amount"]),
                            "distance": float(row["distance"]),
                            "source_id": None,
                            "budget_id": row.get("reference_budget_id"),
                        }
                        for row in rows
                    ],
                },
                step=step,
                estimation_id=estimation_id,
                consensus_fn=distance_weighted_consensus,
            )
            contributions.append(contribution)
            anchors.append(
                {
                    "name": component["name"],
                    "estimated_hours": result.get("estimated_hours"),
                    "reliability": result.get("reliability"),
                    "dispersion": result.get("dispersion"),
                    "has_match": bool(result.get("has_match")),
                }
            )

        # --- phase 2: consolidation ---------------------------------------- #
        anchor_by_name = {a["name"]: a for a in anchors}
        lines: list[str] = []
        for component in components:
            refs = _references_for(component["name"], matches)
            ref_text = ", ".join(f"{h:.0f}h" for h in refs) if refs else "no references"
            anchor = anchor_by_name.get(component["name"]) or {}
            if anchor.get("has_match"):
                anchor_text = (
                    f" | consensus anchor = {anchor['estimated_hours']}h "
                    f"(reliability {anchor.get('reliability')})"
                )
            else:
                anchor_text = " | no consensus anchor (no historical analog)"
            lines.append(
                f"- {component['name']} [{component['category']}]: "
                f"references = {ref_text}{anchor_text}"
            )

        model = settings.GRAPH_GENERATION_MODEL
        started = perf_counter()
        result, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_GENERATE_SYSTEM_PROMPT,
            user_message="Components, their historical reference budgets and the "
            "deterministic consensus anchors:\n" + "\n".join(lines),
            response_model=ConsolidatedEstimate,
            model_override=model,
            max_tokens=_max_tokens_for(model),
            reasoning_effort=_reasoning_effort_for(model),
        )
        contributions.append(
            record_model_action(
                "estimate_generator",
                "consolidate_estimate",
                step=step,
                estimation_id=estimation_id,
                model=model,
                summary=f"total {result.total_engineer_days}d over "
                f"{len(result.components)} components (confidence {result.confidence})",
                duration_ms=int((perf_counter() - started) * 1000),
            )
        )

        log.info(
            "supervisor_agent_estimate_generator",
            components=len(result.components),
            total_engineer_days=result.total_engineer_days,
            anchored=sum(1 for a in anchors if a.get("has_match")),
        )
        return {
            "estimate": result.model_dump(),
            "component_anchors": anchors,
            "agent_contributions": contributions,
        }


# --------------------------------------------------------------------------- #
# competitive_estimate_generator (S14 LIVE) — same node, run as a competition #
# --------------------------------------------------------------------------- #
def _competition_brief(state: SupervisorState, estimate: dict) -> str:
    """The shared evidence both estimators read: components, references, base total."""
    components = state.get("components") or []
    matches = state.get("budget_matches") or []
    lines = ["Project components and their historical reference budgets (engineer-hours):"]
    for component in components:
        refs = _references_for(component["name"], matches)
        ref_text = ", ".join(f"{h:.0f}h" for h in refs) if refs else "no historical references"
        lines.append(f"- {component['name']} [{component['category']}]: {ref_text}")
    lines.append(
        f"\nGrounded consolidation total (engineer-days): {estimate.get('total_engineer_days')}"
    )
    return "\n".join(lines)


async def competitive_estimate_generator(state: SupervisorState) -> dict:
    """The estimate step, run as a COMPETITION instead of a single consolidation.

    Same node NAME as ``estimate_generator`` so the router, the dependency ladder and the
    privilege table are all untouched — only the body changes. Two steps:

    1. Produce the grounded per-component estimate exactly as ``estimate_generator`` does,
       so ``coherence_validator`` reads the same shape it always has.
    2. Run the two-estimator competition subgraph over the same evidence to get a RANGE
       and the arithmetic ``divergence``. The range is attached to the estimate; the
       divergence is written as a state FACT the validator folds into confidence, so a
       wide spread can trip the human gate on its own.
    """
    base = await estimate_generator(state)
    with logfire.span("agent: competitive_estimate_generator"):
        step = _step_of(state)
        estimation_id = state.get("estimation_id")
        brief = _competition_brief(state, base["estimate"])
        sub = await COMPETITION_GRAPH.ainvoke({"brief": brief})
        proposals = sub.get("proposals") or []
        divergence = sub.get("divergence") or compute_divergence(proposals)
        synthesis = sub.get("synthesis") or {}

        estimate = dict(base["estimate"])
        if synthesis:
            estimate["range"] = {"low": synthesis.get("low"), "high": synthesis.get("high")}
            estimate["open_questions"] = synthesis.get("open_questions") or []

        contributions = list(base.get("agent_contributions") or [])
        for proposal in proposals:
            contributions.append(
                record_model_action(
                    "estimate_generator",
                    f"competition_{proposal.get('stance')}",
                    step=step,
                    estimation_id=estimation_id,
                    summary=f"{proposal.get('stance')} total = "
                    f"{proposal.get('total_engineer_days')}d",
                )
            )
        contributions.append(
            record_model_action(
                "estimate_generator",
                "competition_synthesis",
                step=step,
                estimation_id=estimation_id,
                summary=f"range {synthesis.get('low')}..{synthesis.get('high')}d, "
                f"divergence {divergence.get('ratio')} ({divergence.get('level')})",
            )
        )

        log.info(
            "supervisor_agent_competitive_estimate_generator",
            divergence=divergence.get("ratio"),
            level=divergence.get("level"),
        )
        return {
            **base,
            "estimate": estimate,
            "proposals": proposals,
            "divergence": divergence,
            "synthesis": synthesis,
            "agent_contributions": contributions,
        }


# --------------------------------------------------------------------------- #
# persistence_agent (S14 LIVE) — the one WRITE, fully sandboxed                #
# --------------------------------------------------------------------------- #
async def persistence_agent(state: SupervisorState) -> dict:
    """Persist the validated estimate — the ONLY write in the system.

    Isolating the write in its own agent is layer one of the sandbox: the four read
    agents cannot save even by accident, because the capability is not in their grant.
    The write goes through ``execute_guarded`` (privilege + argument + tenancy checks,
    then audit). An irreversible action with no human approval is DEFERRED, not executed
    — which is why the flow forces the gate to pause first: the human pause IS the
    authorisation for the irreversible write. A rejected estimate is therefore never
    persisted.
    """
    with logfire.span("agent: persistence_agent"):
        step = _step_of(state)
        estimation_id = state.get("estimation_id")
        request = ActionRequest(
            agent="persistence_agent",
            tool="save_estimate",
            args={"estimation_id": estimation_id, "estimate": state.get("estimate") or {}},
            estimation_id=estimation_id,
            step=step,
        )
        result, contribution = await execute_guarded(request, state)
        log.info("supervisor_agent_persistence", outcome=contribution.get("outcome"))
        return {"saved": result, "agent_contributions": [contribution]}


# --------------------------------------------------------------------------- #
# coherence_validator — validate_estimate only                                #
# --------------------------------------------------------------------------- #
def _apply_divergence_penalty(confidence: float, divergence: dict | None) -> float:
    """Fold estimator disagreement into the confidence the gate reads.

    A wide spread between the two competing estimators is uncertainty that grounding
    cannot see, so it must be able to trip the human gate on its own. The penalty is
    ``SUPERVISOR_DIVERGENCE_PENALTY * ratio`` where ``ratio`` is arithmetic
    (``competition.compute_divergence``). No divergence (competition off) → no change, so
    the reference path is byte-for-byte unaffected.
    """
    ratio = float((divergence or {}).get("ratio") or 0.0)
    if ratio <= 0.0:
        return confidence
    penalty = get_settings().SUPERVISOR_DIVERGENCE_PENALTY * min(ratio, 1.0)
    return max(0.0, min(1.0, confidence - penalty))


def _confidence_score(estimate: dict, issues: list[str], grounded: int, total: int) -> float:
    """Map the model's label × grounding × guardrail issues onto a 0..1 signal.

    Deterministic ON PURPOSE. The human-review trigger has to be reproducible and
    unit-testable, so the model's self-reported ``"high"`` cannot on its own carry an
    ungrounded estimate past the gate — it is only the starting point, scaled by how
    much of the estimate actually has historical precedent and penalised per guardrail
    issue.
    """
    base = {"low": 0.3, "medium": 0.6, "high": 0.9}.get(estimate.get("confidence"), 0.6)
    ratio = (grounded / total) if total else 0.0
    return max(0.0, min(1.0, base * ratio - 0.1 * len(issues)))


async def coherence_validator(state: SupervisorState) -> dict:
    """Run the guardrails over the estimate and publish the FACTS the gate reads.

    This agent writes facts (``confidence``, ``out_of_range``, ``grounded_components``)
    and never the verdict. The gate owns the verdict — that split is what lets the
    threshold move via configuration without touching the validator.
    """
    with logfire.span("agent: coherence_validator"):
        step = _step_of(state)
        estimation_id = state.get("estimation_id")
        estimate = state.get("estimate") or {}
        matches = state.get("budget_matches") or []
        components = estimate.get("components") or []

        result, contribution = await guarded_dispatch(
            "coherence_validator",
            "validate_estimate",
            {
                "components": [
                    {
                        "name": c.get("name", "?"),
                        "estimated_hours": (c.get("engineer_days") or 0) * HOURS_PER_DAY,
                        "reference_amounts": _references_for(c.get("name", "?"), matches),
                    }
                    for c in components
                ],
                "total_hours": (estimate.get("total_engineer_days") or 0) * HOURS_PER_DAY,
            },
            step=step,
            estimation_id=estimation_id,
        )

        # The deterministic range/sum guardrails, reused verbatim from the pre-exercise
        # node so the two paths cannot drift apart.
        issues = _validate(estimate, matches)
        grounded = sum(1 for c in components if _references_for(c.get("name", "?"), matches))
        total = len(components)
        confidence = _confidence_score(estimate, issues, grounded, total)
        # Competition (S14 live): a wide spread between the two estimators is structural
        # uncertainty grounding cannot see. Fold it into the SAME confidence the gate
        # reads, so divergence alone can trip the pause. Absent when competition is off.
        confidence = _apply_divergence_penalty(confidence, state.get("divergence"))

        log.info(
            "supervisor_agent_coherence_validator",
            issues=len(issues),
            confidence=round(confidence, 3),
            grounded=grounded,
            total=total,
        )
        update: dict = {
            "status": "validated" if not issues else "needs_review",
            "validation": result,
            "confidence": confidence,
            "out_of_range": any("outside the plausible range" in i for i in issues),
            "grounded_components": grounded,
            "agent_contributions": [contribution],
        }
        if issues:
            update["errors"] = issues
        # Sandboxing (S14 live): when an irreversible save is enabled, declare it here so
        # the gate pauses to authorise it (see gate.review_reasons + persistence_agent).
        # The human pause on this estimate doubles as approval of the no-undo write.
        if get_settings().SUPERVISOR_PERSISTENCE_ENABLED:
            update["persist_requested"] = True
        return update
