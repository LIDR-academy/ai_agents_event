"""Multi-index routing in a cascade of increasing cost (Session 10, Article 5).

The corpus is heterogeneous, so searching every collection on every query lets
the dominant type flood the top-k. The router decides WHICH collections to search,
resolving in order — the cheapest signal that suffices wins:

0. **Explicit** — the caller named the collections in the request contract. The
   best router is no router: if the client knows, we trust it and classify nothing.
1. **Rules** — deterministic vocabulary patterns (``collections.match_rules``). A
   query saying "budget"/"cost"/"hours" obviously wants budgets; no LLM needed.
2. **Classifier** — an LLM with structured output emits 1–3 targets plus a
   one-sentence reason. Ambiguity is modelled as MULTIPLE targets, never a
   confidence threshold.
3. **Fallback** — search every collection in parallel. Graceful degradation:
   never worse than the single-index world we came from.

Every decision is logged with its level and reason (attribute, audit, debug).
This is a deliberately bounded precursor to agentic handover: a closed
classification, no open-ended reasoning, no tools.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, Field

from app.config import get_settings
from app.generation.rag.retrieval.collections import (
    ALL_COLLECTIONS,
    Collection,
    match_rules,
)

log = structlog.get_logger()


class RouteClassification(BaseModel):
    """Structured output of the LLM router (closed target set, 1–3 destinations)."""

    targets: list[Collection] = Field(
        min_length=1,
        max_length=3,
        description="Which collections to search. One per genuinely relevant "
        "source type; model ambiguity as several targets, not a maybe.",
    )
    reason: str = Field(description="One sentence justifying the routing choice.")


@dataclass(frozen=True)
class RoutingDecision:
    """Where the query is routed and how that was decided."""

    targets: list[Collection]
    level: str  # "explicit" | "rules" | "classifier" | "fallback"
    reason: str


_CLASSIFIER_SYSTEM_PROMPT = (
    "You route a search query to the document collections most likely to answer "
    "it. The collections are:\n"
    "- budget: historical project budgets/quotes with components, hours and costs.\n"
    "- transcript: client meeting transcripts (what was discussed and decided).\n"
    "- technical_doc: internal technical documentation, specs and API references.\n"
    "Return between 1 and 3 targets. Pick MORE than one only when the query "
    "genuinely spans several source types; ambiguity is several targets, not a "
    "guess. Always add a one-sentence reason. Never invent collections."
)


async def route(
    query_text: str,
    *,
    explicit: list[Collection] | None = None,
    rules_enabled: bool = True,
    classifier_enabled: bool = True,
    max_targets: int = 3,
    model: str | None = None,
) -> RoutingDecision:
    """Resolve the target collections through the cascade described above."""
    # Level 0 — explicit contract.
    if explicit:
        targets = list(dict.fromkeys(explicit))[:max_targets]
        decision = RoutingDecision(targets, "explicit", "Caller specified collections.")
        log.info("routing_decided", level=decision.level, targets=[t.value for t in targets])
        return decision

    # Level 1 — deterministic vocabulary rules.
    if rules_enabled:
        hits = match_rules(query_text)
        if hits:
            targets = hits[:max_targets]
            reason = f"Vocabulary matched collections: {', '.join(t.value for t in targets)}."
            decision = RoutingDecision(targets, "rules", reason)
            log.info(
                "routing_decided", level="rules", targets=[t.value for t in targets], reason=reason
            )
            return decision

    # Level 2 — LLM classifier with structured output.
    if classifier_enabled:
        try:
            classification = await _classify(query_text, model=model)
            targets = list(dict.fromkeys(classification.targets))[:max_targets]
            decision = RoutingDecision(targets, "classifier", classification.reason)
            log.info(
                "routing_decided",
                level="classifier",
                targets=[t.value for t in targets],
                reason=classification.reason,
            )
            return decision
        except Exception as exc:  # noqa: BLE001 — classifier failure degrades to fallback.
            log.warning(
                "routing_classifier_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )

    # Level 3 — fallback: search everything (graceful degradation).
    targets = list(ALL_COLLECTIONS)
    decision = RoutingDecision(
        targets, "fallback", "No rule or classifier signal; searching all collections."
    )
    log.info("routing_decided", level="fallback", targets=[t.value for t in targets])
    return decision


async def _classify(query_text: str, *, model: str | None) -> RouteClassification:
    """Run the LLM router classifier (structured output via ``LLMWrapper``)."""
    from app.dependencies import get_llm_wrapper

    settings = get_settings()
    wrapper = get_llm_wrapper()
    classification, _meta = await asyncio.to_thread(
        wrapper.complete_structured,
        system_prompt=_CLASSIFIER_SYSTEM_PROMPT,
        user_message=query_text,
        response_model=RouteClassification,
        model_override=model or settings.ROUTER_MODEL,
        max_tokens=1000,
    )
    return classification
