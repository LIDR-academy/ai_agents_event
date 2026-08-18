"""Unit tests for the runtime tier resolver."""

from __future__ import annotations

from app.generation.conversation.models import ProjectMetadata
from app.generation.conversation.tier_resolver import Tier, resolve_tier


def test_default_tier_for_neutral_input() -> None:
    tier, rule = resolve_tier(
        transcript="A CRM for the sales team with contacts and pipelines.",
        metadata=ProjectMetadata(),
    )
    assert tier is Tier.DEFAULT
    assert rule == "default"


def test_explicit_override_beats_every_rule() -> None:
    tier, rule = resolve_tier(
        transcript="We signed the NDA and run HIPAA workloads.",
        metadata=ProjectMetadata(),
        override=Tier.PM,
    )
    assert tier is Tier.PM
    assert rule == "explicit_override"


def test_nda_in_transcript_promotes_to_executive() -> None:
    tier, rule = resolve_tier(
        transcript="Heads-up, we signed an NDA before this call.",
        metadata=ProjectMetadata(),
    )
    assert tier is Tier.EXECUTIVE
    assert rule == "nda_detected"


def test_nda_in_agreed_scope_promotes_to_executive() -> None:
    tier, rule = resolve_tier(
        transcript="Just a follow up.",
        metadata=ProjectMetadata(agreed_scope="Under embargo until Q4."),
    )
    assert tier is Tier.EXECUTIVE
    assert rule == "nda_detected"


def test_regulatory_keyword_promotes_to_executive() -> None:
    tier, rule = resolve_tier(
        transcript="The platform must be HIPAA-compliant from day one.",
        metadata=ProjectMetadata(),
    )
    assert tier is Tier.EXECUTIVE
    assert rule == "regulatory_context"


def test_two_or_more_dev_keywords_promote_to_developer() -> None:
    tier, rule = resolve_tier(
        transcript="We run microservices on Kubernetes with a Terraform pipeline.",
        metadata=ProjectMetadata(),
    )
    assert tier is Tier.DEVELOPER
    assert rule == "technical_audience"


def test_single_dev_keyword_is_not_enough_for_developer() -> None:
    tier, _ = resolve_tier(
        transcript="We deploy with docker today, that's it.",
        metadata=ProjectMetadata(),
    )
    assert tier is Tier.DEFAULT


def test_small_team_metadata_promotes_to_pm() -> None:
    tier, rule = resolve_tier(
        transcript="Just two of us building this MVP.",
        metadata=ProjectMetadata(assumed_team_size=2),
    )
    assert tier is Tier.PM
    assert rule == "low_budget_pm"


def test_nda_beats_small_team() -> None:
    """NDA precedes the PM rule in the chain — first match wins."""
    tier, rule = resolve_tier(
        transcript="Just two of us — and we signed an NDA last week.",
        metadata=ProjectMetadata(assumed_team_size=2),
    )
    assert tier is Tier.EXECUTIVE
    assert rule == "nda_detected"


def test_regulatory_tech_string_in_metadata_promotes() -> None:
    tier, rule = resolve_tier(
        transcript="Migrate to a new payments stack.",
        metadata=ProjectMetadata(mentioned_technologies=["Stripe", "PCI-DSS"]),
    )
    assert tier is Tier.EXECUTIVE
    assert rule == "regulatory_context"
