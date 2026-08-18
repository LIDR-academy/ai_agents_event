"""Three multi-turn synthetic scenarios with fact-trackers.

Each scenario is a fixed sequence of ``ScenarioTurn`` items. The runner
walks them in order against a single session, so turn N sees the
compressed view of turns 1..N-1. The ``fact_introduced`` field on each
turn declares what fact the runner should later check against
``MemoryDriftMetric`` for subsequent turns.

The three scenarios target distinct failure modes of the CAG:

- ``growing``        — the session monotonically accretes requirements.
                       Stresses sliding-window eviction: by turn 20 the
                       turn-1 anchor information needs to survive either
                       as an explicit anchor or inside the cumulative
                       summary.
- ``pivot``          — turn 5 swaps the stack from React to Flutter.
                       Tests whether ``mentioned_technologies`` accretes
                       both (correct) or loses React (drift).
- ``contradiction``  — turn 3 says budget 30k, turn 8 says budget 80k.
                       Tests which version sticks in the metadata and/or
                       summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.domain.schemas.estimation import DetailLevel, OutputFormat, ProjectType
from evals.stress.metrics import FactField


@dataclass(frozen=True)
class ScenarioTurn:
    """One conversational turn in a scripted scenario.

    ``fact_introduced`` is the literal substring the runner will look for
    in *later* turns' session snapshots via ``MemoryDriftMetric``. If
    ``None``, the turn carries no fact worth tracking. ``fact_field`` tells
    the metric where to scope the search (e.g. ``"project_name"`` for the
    name introduced at turn 1).
    """

    transcript: str
    fact_introduced: str | None = None
    fact_field: FactField = "any"


@dataclass(frozen=True)
class Scenario:
    name: Literal["growing", "pivot", "contradiction"]
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
    turns: list[ScenarioTurn] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.turns) < 20:
            raise ValueError(
                f"scenario {self.name!r} has {len(self.turns)} turns; "
                "the exercise mandates at least 20 to exercise the sliding "
                "window past its cap"
            )


# --- growing ----------------------------------------------------------------


_GROWING_TURNS: list[ScenarioTurn] = [
    ScenarioTurn(
        "We want a B2B SaaS called Nimbus for procurement teams. Stack: React + "
        "Postgres. Team of 3. Initial scope: vendor catalogue, RFQ workflow, "
        "basic dashboards.",
        fact_introduced="Nimbus",
        fact_field="project_name",
    ),
    ScenarioTurn(
        "Add SSO with Okta. Single sign-on must work for both employees and "
        "external vendor users on the same domain.",
        fact_introduced="Okta",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Confirmed budget: 60k EUR for the first release. Team stays at 3.",
        fact_introduced="60",
        fact_field="any",
    ),
    ScenarioTurn(
        "We need an audit log of every approval, with WORM storage for 7 years "
        "for compliance with internal policy.",
        fact_introduced="audit log",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add CSV import for legacy vendor master data, ~50k rows expected.",
        fact_introduced="CSV import",
        fact_field="any",
    ),
    ScenarioTurn(
        "Multi-tenant: we'll resell this to two more sister companies. Strict "
        "data isolation per tenant.",
        fact_introduced="multi-tenant",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add notification fan-out via email and Microsoft Teams webhook.",
        fact_introduced="Teams webhook",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Public read-only API for partners to query catalog availability. "
        "OAuth2 client credentials.",
        fact_introduced="OAuth2",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add a reporting module: aggregated KPIs by buyer and category, "
        "weekly digest exported to PDF.",
        fact_introduced="weekly digest",
        fact_field="any",
    ),
    ScenarioTurn(
        "Internationalisation: ES, EN, PT. Currency must follow the tenant.",
        fact_introduced="ES, EN, PT",
        fact_field="any",
    ),
    ScenarioTurn(
        "Mobile-friendly responsive UI for approvers, but no native app yet.",
        fact_introduced="responsive",
        fact_field="any",
    ),
    ScenarioTurn(
        "Background job system for nightly reconciliation against the ERP "
        "(SAP) using IDoc files.",
        fact_introduced="SAP",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Two-factor auth for any user with approval rights above 10k EUR.",
        fact_introduced="two-factor",
        fact_field="any",
    ),
    ScenarioTurn(
        "Bulk PO export to XML following an UBL profile the procurement team "
        "shared.",
        fact_introduced="UBL",
        fact_field="any",
    ),
    ScenarioTurn(
        "GDPR: right-to-erasure flow for vendor contacts on request.",
        fact_introduced="GDPR",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add a per-tenant feature flag for an experimental AI suggestion "
        "panel that recommends similar past PRs.",
        fact_introduced="feature flag",
        fact_field="any",
    ),
    ScenarioTurn(
        "Vendor self-service portal: vendors update their own banking and "
        "tax info, with admin approval.",
        fact_introduced="self-service portal",
        fact_field="any",
    ),
    ScenarioTurn(
        "Performance SLA: P95 page load under 2s with 200 concurrent users.",
        fact_introduced="P95",
        fact_field="any",
    ),
    ScenarioTurn(
        "Disaster recovery: RPO 1 hour, RTO 4 hours. Daily cross-region "
        "Postgres snapshots.",
        fact_introduced="RPO",
        fact_field="any",
    ),
    ScenarioTurn(
        "Final ask: integration with DocuSign for contract counter-signature "
        "right after vendor approval.",
        fact_introduced="DocuSign",
        fact_field="technologies",
    ),
]


# --- pivot ------------------------------------------------------------------


_PIVOT_TURNS: list[ScenarioTurn] = [
    ScenarioTurn(
        "Build a customer-facing app called Helios for a retail chain. "
        "Stack: React Native targeting iOS and Android. Team of 4.",
        fact_introduced="Helios",
        fact_field="project_name",
    ),
    ScenarioTurn(
        "Loyalty card replacement: members scan QR at checkout, accrue points, "
        "redeem in-app.",
        fact_introduced="loyalty",
        fact_field="any",
    ),
    ScenarioTurn(
        "Backend: a Node.js API on top of the existing Postgres customer DB.",
        fact_introduced="Node.js",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Push notifications for offers segmented by store region.",
        fact_introduced="push notifications",
        fact_field="any",
    ),
    ScenarioTurn(
        "PIVOT: the client just announced they will standardise on Flutter "
        "across all mobile properties. We switch the stack to Flutter. "
        "React Native is OFF the table.",
        fact_introduced="Flutter",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Re-confirmed Flutter for both iOS and Android; same scope as before.",
        fact_introduced="Flutter",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Add Apple Wallet and Google Wallet passes for the loyalty card.",
        fact_introduced="Apple Wallet",
        fact_field="any",
    ),
    ScenarioTurn(
        "Integration with the existing PoS API (REST/JSON) for point accrual.",
        fact_introduced="PoS API",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add bar-code scanning for in-store products to show item details.",
        fact_introduced="bar-code",
        fact_field="any",
    ),
    ScenarioTurn(
        "Geofenced offers: deliver a push when a member is near a store.",
        fact_introduced="geofenced",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add social login (Apple ID, Google).",
        fact_introduced="Apple ID",
        fact_field="any",
    ),
    ScenarioTurn(
        "Internal admin panel (web) for marketers to schedule campaigns. "
        "Vue.js for the admin side.",
        fact_introduced="Vue.js",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Analytics: events to Segment + downstream to BigQuery.",
        fact_introduced="Segment",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Add in-app messaging for customer support, integrated with Zendesk.",
        fact_introduced="Zendesk",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Feature: digital receipts that survive even if the printer fails at "
        "the till.",
        fact_introduced="digital receipts",
        fact_field="any",
    ),
    ScenarioTurn(
        "Localisation: ES and CA only; no English in v1.",
        fact_introduced="ES and CA",
        fact_field="any",
    ),
    ScenarioTurn(
        "Crash reporting and performance traces via Firebase Crashlytics.",
        fact_introduced="Crashlytics",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "App store presence: ASO assets and screenshots for 3 device sizes.",
        fact_introduced="ASO",
        fact_field="any",
    ),
    ScenarioTurn(
        "Compliance: GDPR consent flow on first open.",
        fact_introduced="GDPR",
        fact_field="any",
    ),
    ScenarioTurn(
        "Final ask: add an offline mode that lets members view their card "
        "and recent rewards without connectivity.",
        fact_introduced="offline mode",
        fact_field="any",
    ),
]


# --- contradiction ----------------------------------------------------------


_CONTRADICTION_TURNS: list[ScenarioTurn] = [
    ScenarioTurn(
        "Build an internal tool called Atlas for the finance department. "
        "Approvals workflow for AP invoices. Stack: Rails + Postgres.",
        fact_introduced="Atlas",
        fact_field="project_name",
    ),
    ScenarioTurn(
        "Team is 2 developers plus a part-time designer.",
        fact_introduced="2 developers",
        fact_field="any",
    ),
    ScenarioTurn(
        "Budget cap announced: 30000 EUR for the whole engagement.",
        fact_introduced="30",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add an audit log of who approved what, exportable to CSV.",
        fact_introduced="audit log",
        fact_field="any",
    ),
    ScenarioTurn(
        "Integration with the existing SSO (Azure AD) — every approver must "
        "authenticate via SSO.",
        fact_introduced="Azure AD",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Add a dashboard with pending approvals grouped by approver.",
        fact_introduced="dashboard",
        fact_field="any",
    ),
    ScenarioTurn(
        "Bulk approve up to 50 invoices in one action, with a confirmation "
        "step.",
        fact_introduced="bulk approve",
        fact_field="any",
    ),
    ScenarioTurn(
        "CONTRADICTION: leadership revised the budget upward. The new budget "
        "is 80000 EUR; the earlier 30000 figure is obsolete. Plan accordingly.",
        fact_introduced="80",
        fact_field="any",
    ),
    ScenarioTurn(
        "Confirmed new budget: 80000 EUR. The earlier 30k was a placeholder.",
        fact_introduced="80",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add three-way match against POs and goods receipts.",
        fact_introduced="three-way match",
        fact_field="any",
    ),
    ScenarioTurn(
        "Integration with the ERP (Oracle EBS) over its REST gateway.",
        fact_introduced="Oracle EBS",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Add per-cost-centre routing of invoices to the correct approver.",
        fact_introduced="cost-centre",
        fact_field="any",
    ),
    ScenarioTurn(
        "Email digest every morning summarising pending approvals per approver.",
        fact_introduced="email digest",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add OCR-assisted data entry for paper invoices using AWS Textract.",
        fact_introduced="Textract",
        fact_field="technologies",
    ),
    ScenarioTurn(
        "Support multi-currency invoices with daily ECB rates for normalisation.",
        fact_introduced="multi-currency",
        fact_field="any",
    ),
    ScenarioTurn(
        "Reports module: aging buckets and total exposure per supplier.",
        fact_introduced="aging buckets",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add comments thread on each invoice for approver back-and-forth.",
        fact_introduced="comments thread",
        fact_field="any",
    ),
    ScenarioTurn(
        "Performance: handle 5000 invoices per day at peak month-end.",
        fact_introduced="5000 invoices",
        fact_field="any",
    ),
    ScenarioTurn(
        "Add 4-eyes-principle for invoices above 25000 EUR.",
        fact_introduced="4-eyes",
        fact_field="any",
    ),
    ScenarioTurn(
        "Final ask: nightly export of approved invoices to the data warehouse "
        "(Snowflake).",
        fact_introduced="Snowflake",
        fact_field="technologies",
    ),
]


_SCENARIOS: dict[str, Scenario] = {
    "growing": Scenario(
        name="growing",
        project_type=ProjectType.WEB_SAAS,
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
        turns=_GROWING_TURNS,
    ),
    "pivot": Scenario(
        name="pivot",
        project_type=ProjectType.MOBILE_APP,
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
        turns=_PIVOT_TURNS,
    ),
    "contradiction": Scenario(
        name="contradiction",
        project_type=ProjectType.INTERNAL_TOOL,
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
        turns=_CONTRADICTION_TURNS,
    ),
}


def get_scenario(name: str) -> Scenario:
    try:
        return _SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown scenario {name!r}; available: {sorted(_SCENARIOS)}"
        ) from exc


def all_scenarios() -> list[Scenario]:
    """Return the three scenarios in deterministic order."""
    return [_SCENARIOS["growing"], _SCENARIOS["pivot"], _SCENARIOS["contradiction"]]
