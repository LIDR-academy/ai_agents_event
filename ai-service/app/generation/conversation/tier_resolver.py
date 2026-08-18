"""Derive the audience tier at runtime from the conversation context.

Tiers shape the prompt's framing (executive bullets vs PM milestones vs
developer-grade detail vs default). Two ways to set them:

- **Explicit override** by the caller (API param ``tier=executive``). Always wins.
- **Implicit derivation** from the latest transcript + accumulated metadata.

The derivation is a precedence-ordered chain of pure-function rules. Each
rule returns ``True``/``False``. The first match decides the tier; if none
matches, the tier is ``DEFAULT``. The resolver returns both the tier and
the **rule name** that fired — that explainability is what lets the side
panel show "executive (nda_detected)" instead of just "executive".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import structlog

from app.generation.conversation.models import ProjectMetadata

log = structlog.get_logger()


class Tier(str, Enum):
    EXECUTIVE = "executive"
    PM = "pm"
    DEVELOPER = "developer"
    DEFAULT = "default"


@dataclass
class ResolutionContext:
    transcript: str
    metadata: ProjectMetadata
    override: Tier | None = None


@dataclass
class TierRule:
    name: str
    tier: Tier
    predicate: Callable[[ResolutionContext], bool]


# --- Predicates --------------------------------------------------------------

_NDA_PATTERN = re.compile(
    r"\b(nda|non[- ]?disclosure|confidential|under embargo|legal hold)\b", re.IGNORECASE
)
_REGULATORY_PATTERN = re.compile(
    r"\b(hipaa|gdpr|sox|pci[- ]?dss|fda|iso[- ]?27001|ccpa)\b", re.IGNORECASE
)
_DEV_KEYWORDS_PATTERN = re.compile(
    r"\b(docker|kubernetes|k8s|microservice|microservices|terraform|iac|"
    r"helm|grpc|graphql|kafka|airflow|spark|rabbitmq)\b",
    re.IGNORECASE,
)


def _has_nda(ctx: ResolutionContext) -> bool:
    if _NDA_PATTERN.search(ctx.transcript):
        return True
    if ctx.metadata.agreed_scope and _NDA_PATTERN.search(ctx.metadata.agreed_scope):
        return True
    return False


def _has_regulatory_context(ctx: ResolutionContext) -> bool:
    if _REGULATORY_PATTERN.search(ctx.transcript):
        return True
    if ctx.metadata.agreed_scope and _REGULATORY_PATTERN.search(ctx.metadata.agreed_scope):
        return True
    for tech in ctx.metadata.mentioned_technologies:
        if _REGULATORY_PATTERN.search(tech):
            return True
    return False


def _is_small_team(ctx: ResolutionContext) -> bool:
    return ctx.metadata.assumed_team_size is not None and ctx.metadata.assumed_team_size <= 2


def _technical_audience(ctx: ResolutionContext) -> bool:
    hits = _DEV_KEYWORDS_PATTERN.findall(ctx.transcript)
    # Require at least two distinct technical keywords to flip — single
    # mentions ("we run on docker") are too noisy for tier promotion.
    return len(set(t.lower() for t in hits)) >= 2


# --- Rule chain --------------------------------------------------------------

_RULES: tuple[TierRule, ...] = (
    TierRule("nda_detected", Tier.EXECUTIVE, _has_nda),
    TierRule("regulatory_context", Tier.EXECUTIVE, _has_regulatory_context),
    TierRule("technical_audience", Tier.DEVELOPER, _technical_audience),
    TierRule("low_budget_pm", Tier.PM, _is_small_team),
)


def resolve_tier(
    *,
    transcript: str,
    metadata: ProjectMetadata,
    override: Tier | None = None,
) -> tuple[Tier, str]:
    """Return ``(tier, rule_name)``.

    Precedence:
        1. ``override`` (caller-provided) wins.
        2. Else, the rule chain is evaluated in order — first hit wins.
        3. Else, ``DEFAULT``.
    """
    ctx = ResolutionContext(transcript=transcript, metadata=metadata, override=override)

    if override is not None:
        log.info("tier_resolved", tier=override.value, rule="explicit_override")
        return override, "explicit_override"

    for rule in _RULES:
        try:
            if rule.predicate(ctx):
                log.info("tier_resolved", tier=rule.tier.value, rule=rule.name)
                return rule.tier, rule.name
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "tier_rule_predicate_failed",
                rule=rule.name,
                error_type=type(exc).__name__,
                error=str(exc)[:120],
            )

    log.info("tier_resolved", tier=Tier.DEFAULT.value, rule="default")
    return Tier.DEFAULT, "default"
