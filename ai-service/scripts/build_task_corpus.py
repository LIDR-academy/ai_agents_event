#!/usr/bin/env python3
"""Generate a TASK-granular historical corpus and (optionally) ingest it (S09).

The Session 8 corpus (``data/budgets_sample.json``) is coarse — one chunk per
budget *component*. The Session 9 estimate is a module → task breakdown, so to
ground fine-grained tasks we need historical evidence at the same granularity.

This script synthesises realistic historical projects, each decomposed into
functional MODULES and, within each module, concrete TASKS with hours. Every
task becomes a :class:`BudgetComponent` tagged with its ``module``, so the
existing structural chunker emits one chunk per task. Generation is fully
offline and DETERMINISTIC (seeded), so re-running yields the same corpus and the
idempotent ingest never duplicates.

Usage::

    # generate data/task_corpus.json only (review before ingesting)
    docker compose exec ai-service python scripts/build_task_corpus.py --generate-only

    # generate + ingest into pgvector (needs OPENAI_API_KEY for embeddings)
    docker compose exec ai-service python scripts/build_task_corpus.py --ingest

    # from the host with the API on localhost:8000
    uv run python scripts/build_task_corpus.py --ingest

Wipe the corpus anytime (it is opt-in, tagged by document_type)::

    DELETE FROM documents WHERE document_type = 'historical_task_breakdown';
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "task_corpus.json"
DOCUMENT_TYPE = "historical_task_breakdown"
CHUNK_TYPE = "historical_task"
CANDIDATE_BASE_URLS = ("http://localhost:8000", "http://ai-service:8000")
DEFAULT_SEED = 90
DEFAULT_COUNT = 60

# --- task templates -------------------------------------------------------
# Each template: (task name, description, tech pool, (min_hours, max_hours),
# complexity). Modules common to every sector plus sector-specific blocks.
# A "task" maps 1:1 to a BudgetComponent tagged with its module.

Template = tuple[str, str, list[str], tuple[int, int], str]

COMMON_MODULES: dict[str, list[Template]] = {
    "Authentication & Access": [
        (
            "OAuth2 / OIDC login",
            "Authorization-code flow with refresh tokens and session handling.",
            ["python", "postgresql", "redis"],
            (24, 56),
            "high",
        ),
        (
            "Role-based access control",
            "Roles, permissions and policy checks across the API.",
            ["python", "postgresql"],
            (16, 40),
            "medium",
        ),
        (
            "SSO integration",
            "SAML/OIDC single sign-on against the client identity provider.",
            ["python", "saml"],
            (16, 32),
            "medium",
        ),
        (
            "Password & MFA flows",
            "Reset, lockout and TOTP-based multi-factor authentication.",
            ["python", "redis"],
            (12, 28),
            "medium",
        ),
    ],
    "Data & Integrations": [
        (
            "Third-party API integration",
            "Resilient client with retries, backoff and idempotency.",
            ["python", "httpx"],
            (16, 40),
            "medium",
        ),
        (
            "ETL / data sync job",
            "Scheduled extract-transform-load between systems.",
            ["python", "airflow", "postgresql"],
            (24, 56),
            "high",
        ),
        (
            "Webhook ingestion",
            "Signed-webhook receiver with replay protection.",
            ["python", "redis"],
            (12, 24),
            "low",
        ),
        (
            "Reporting data model",
            "Star-schema tables and aggregation views for analytics.",
            ["postgresql", "dbt"],
            (16, 40),
            "medium",
        ),
    ],
    "Frontend / UX": [
        (
            "Core SPA scaffolding",
            "App shell, routing, state management and design system.",
            ["typescript", "react"],
            (24, 48),
            "medium",
        ),
        (
            "Dashboard & charts",
            "Operational dashboard with filters and exportable charts.",
            ["typescript", "react", "d3"],
            (24, 56),
            "high",
        ),
        (
            "Form flows & validation",
            "Multi-step forms with client and server validation.",
            ["typescript", "react"],
            (12, 32),
            "medium",
        ),
        (
            "Accessibility & i18n",
            "WCAG pass and internationalisation of the UI.",
            ["typescript"],
            (12, 24),
            "low",
        ),
    ],
    "Infrastructure & DevOps": [
        (
            "CI/CD pipeline",
            "Build, test and deploy pipeline with environments.",
            ["docker", "github_actions"],
            (16, 36),
            "medium",
        ),
        (
            "IaC provisioning",
            "Infrastructure-as-code for the cloud footprint.",
            ["terraform", "aws"],
            (24, 48),
            "high",
        ),
        (
            "Observability stack",
            "Structured logs, metrics and alerting.",
            ["prometheus", "grafana"],
            (16, 32),
            "medium",
        ),
        (
            "Container orchestration",
            "Kubernetes manifests, autoscaling and rollout strategy.",
            ["kubernetes", "docker"],
            (24, 56),
            "high",
        ),
    ],
    "Security & Compliance": [
        (
            "Audit logging",
            "Tamper-evident audit trail of sensitive operations.",
            ["python", "postgresql"],
            (12, 28),
            "medium",
        ),
        (
            "Data encryption",
            "Encryption at rest and in transit, key rotation.",
            ["python", "kms"],
            (12, 32),
            "medium",
        ),
        (
            "Pentest remediation",
            "Fixing findings from a third-party security review.",
            ["python"],
            (16, 40),
            "high",
        ),
    ],
    "QA & Testing": [
        (
            "Automated test suite",
            "Unit and integration coverage for the core flows.",
            ["pytest"],
            (24, 56),
            "medium",
        ),
        (
            "End-to-end tests",
            "Browser-level E2E of the critical user journeys.",
            ["playwright"],
            (16, 36),
            "medium",
        ),
        (
            "Load & performance testing",
            "Throughput and latency benchmarks under load.",
            ["k6"],
            (12, 28),
            "medium",
        ),
    ],
    "Project Management": [
        (
            "Discovery & requirements",
            "Workshops, scope definition and acceptance criteria.",
            ["confluence"],
            (16, 40),
            "low",
        ),
        (
            "Coordination & reporting",
            "Sprint ceremonies, stakeholder reporting and risk tracking.",
            ["jira"],
            (24, 60),
            "low",
        ),
        (
            "UAT & handover",
            "User-acceptance test rounds, training and documentation handover.",
            ["confluence"],
            (16, 36),
            "low",
        ),
    ],
    "Analytics & Reporting": [
        (
            "Event tracking & instrumentation",
            "Product analytics events with a tracking plan.",
            ["segment", "typescript"],
            (16, 36),
            "medium",
        ),
        (
            "BI dashboards",
            "Self-service dashboards and scheduled report delivery.",
            ["metabase", "postgresql"],
            (24, 48),
            "medium",
        ),
        (
            "Data warehouse modelling",
            "Modelled marts and transformations for analytics.",
            ["dbt", "bigquery"],
            (24, 56),
            "high",
        ),
    ],
    "Notifications & Messaging": [
        (
            "Transactional email & SMS",
            "Templated multi-channel notifications with delivery tracking.",
            ["python", "sendgrid", "twilio"],
            (12, 28),
            "low",
        ),
        (
            "Push notifications",
            "Mobile/web push with topic subscriptions and opt-out.",
            ["python", "firebase"],
            (12, 28),
            "medium",
        ),
        (
            "In-app notification centre",
            "Real-time in-app inbox with read/unread state.",
            ["typescript", "websocket", "redis"],
            (16, 36),
            "medium",
        ),
    ],
    "Search & Discovery": [
        (
            "Full-text search service",
            "Indexing pipeline and query API with ranking.",
            ["elasticsearch", "python"],
            (24, 56),
            "high",
        ),
        (
            "Recommendations engine",
            "Collaborative/content-based recommendations.",
            ["python", "scikit_learn"],
            (24, 56),
            "high",
        ),
        (
            "Autocomplete & typeahead",
            "Low-latency suggestions with synonyms.",
            ["elasticsearch", "redis"],
            (12, 28),
            "medium",
        ),
    ],
    "Admin & Back-office": [
        (
            "Admin console",
            "CRUD back-office with auditing and bulk actions.",
            ["typescript", "react", "python"],
            (24, 48),
            "medium",
        ),
        (
            "Feature flags & config",
            "Runtime feature flags and remote configuration.",
            ["python", "redis"],
            (12, 28),
            "medium",
        ),
        (
            "Bulk import/export",
            "CSV/Excel import-export with validation and previews.",
            ["python", "pandas"],
            (12, 32),
            "medium",
        ),
    ],
    "Integrations Platform": [
        (
            "Public REST/GraphQL API",
            "Versioned public API with rate limiting and docs.",
            ["python", "fastapi"],
            (24, 56),
            "high",
        ),
        (
            "OAuth provider / API keys",
            "Issue and manage third-party API credentials.",
            ["python", "postgresql"],
            (16, 40),
            "high",
        ),
        (
            "Event streaming bus",
            "Internal event bus with schemas and dead-letter handling.",
            ["python", "kafka"],
            (24, 56),
            "high",
        ),
    ],
}

SECTOR_MODULES: dict[str, dict[str, list[Template]]] = {
    "finance": {
        "Payments & Billing": [
            (
                "Payment gateway integration",
                "Card payments via a PSP with 3-D Secure.",
                ["python", "stripe"],
                (24, 56),
                "high",
            ),
            (
                "Ledger & reconciliation",
                "Double-entry ledger and daily reconciliation.",
                ["python", "postgresql"],
                (32, 72),
                "high",
            ),
            (
                "Subscription billing",
                "Plans, proration and dunning for recurring billing.",
                ["python", "stripe"],
                (24, 48),
                "medium",
            ),
        ],
        "Regulatory Reporting": [
            (
                "KYC / AML onboarding",
                "Identity verification and sanction-list screening.",
                ["python", "onfido"],
                (32, 64),
                "high",
            ),
            (
                "Regulatory report export",
                "Scheduled regulator-format report generation.",
                ["python", "postgresql"],
                (16, 40),
                "medium",
            ),
        ],
    },
    "ecommerce": {
        "Catalog & Search": [
            (
                "Product catalog model",
                "Categories, variants and inventory tracking.",
                ["python", "postgresql"],
                (24, 48),
                "medium",
            ),
            (
                "Faceted search",
                "Search with filters, facets and relevance tuning.",
                ["elasticsearch"],
                (24, 56),
                "high",
            ),
        ],
        "Checkout & Cart": [
            (
                "Cart & checkout flow",
                "Cart, address, shipping and payment steps.",
                ["typescript", "react", "stripe"],
                (24, 56),
                "high",
            ),
            (
                "Promotions & coupons",
                "Discount rules, coupons and loyalty points.",
                ["python", "redis"],
                (16, 36),
                "medium",
            ),
            (
                "Order management",
                "Order lifecycle, fulfilment and returns.",
                ["python", "postgresql"],
                (24, 48),
                "medium",
            ),
        ],
    },
    "healthcare": {
        "Clinical Records": [
            (
                "Patient records (EHR)",
                "Encrypted patient records with access controls.",
                ["python", "postgresql"],
                (32, 72),
                "high",
            ),
            (
                "HL7 / FHIR integration",
                "Interoperability with hospital systems via FHIR.",
                ["python", "fhir"],
                (32, 64),
                "high",
            ),
        ],
        "Scheduling": [
            (
                "Appointment scheduling",
                "Calendars, availability and reminders.",
                ["python", "postgresql"],
                (24, 48),
                "medium",
            ),
            (
                "Telemedicine session",
                "Video consultation with waiting room.",
                ["typescript", "webrtc"],
                (24, 56),
                "high",
            ),
        ],
    },
    "industrial": {
        "Telemetry & IoT": [
            (
                "Device telemetry ingestion",
                "High-throughput sensor data ingestion.",
                ["python", "kafka"],
                (32, 64),
                "high",
            ),
            (
                "Time-series storage",
                "Downsampling and retention of time-series data.",
                ["timescaledb"],
                (16, 40),
                "medium",
            ),
        ],
        "Operations": [
            (
                "Production scheduling",
                "Job scheduling and shop-floor sequencing.",
                ["python", "postgresql"],
                (24, 56),
                "high",
            ),
            (
                "Predictive maintenance",
                "Anomaly detection on equipment signals.",
                ["python", "scikit_learn"],
                (24, 48),
                "high",
            ),
        ],
    },
    "logistics": {
        "Fleet & Routing": [
            (
                "Route optimization engine",
                "Vehicle routing with time windows and constraints.",
                ["python", "or_tools"],
                (32, 72),
                "high",
            ),
            (
                "Live fleet tracking",
                "Real-time GPS tracking and ETA computation.",
                ["python", "kafka", "postgresql"],
                (24, 56),
                "high",
            ),
            (
                "Driver mobile app integration",
                "Job dispatch and proof-of-delivery capture.",
                ["typescript", "react_native"],
                (24, 48),
                "medium",
            ),
        ],
        "Warehouse & Inventory": [
            (
                "Inventory & stock control",
                "Multi-warehouse stock levels and reservations.",
                ["python", "postgresql"],
                (24, 56),
                "high",
            ),
            (
                "Barcode / scanning workflow",
                "Pick-pack-ship flows with handheld scanners.",
                ["python", "redis"],
                (16, 40),
                "medium",
            ),
            (
                "Carrier & label integration",
                "Multi-carrier rates, labels and tracking sync.",
                ["python", "httpx"],
                (16, 40),
                "medium",
            ),
        ],
    },
    "education": {
        "Courses & LMS": [
            (
                "Course authoring & catalog",
                "Course builder, modules, lessons and catalog.",
                ["typescript", "react", "postgresql"],
                (24, 56),
                "high",
            ),
            (
                "Video lessons & progress",
                "Lesson player with resume and progress tracking.",
                ["typescript", "webrtc"],
                (24, 48),
                "medium",
            ),
            (
                "Certificates & badges",
                "Completion certificates and shareable badges.",
                ["python", "postgresql"],
                (12, 28),
                "low",
            ),
        ],
        "Assessments": [
            (
                "Quiz & exam engine",
                "Question banks, timed exams and auto-grading.",
                ["python", "postgresql"],
                (24, 56),
                "high",
            ),
            (
                "Plagiarism & proctoring",
                "Similarity checks and remote proctoring hooks.",
                ["python", "scikit_learn"],
                (24, 48),
                "high",
            ),
            (
                "Grades & feedback",
                "Gradebook with rubric-based feedback.",
                ["python", "postgresql"],
                (16, 36),
                "medium",
            ),
        ],
    },
    "media": {
        "Content & Publishing": [
            (
                "Headless CMS",
                "Structured content modelling with a publishing workflow.",
                ["typescript", "graphql", "postgresql"],
                (24, 56),
                "high",
            ),
            (
                "Digital asset management",
                "Media library with transcoding and metadata.",
                ["python", "ffmpeg", "s3"],
                (24, 56),
                "high",
            ),
            (
                "Editorial workflow",
                "Draft-review-publish with roles and scheduling.",
                ["typescript", "react"],
                (16, 40),
                "medium",
            ),
        ],
        "Streaming & Playback": [
            (
                "Adaptive video streaming",
                "HLS/DASH packaging and adaptive bitrate playback.",
                ["python", "ffmpeg"],
                (32, 72),
                "high",
            ),
            (
                "Live streaming pipeline",
                "Low-latency live ingest and distribution.",
                ["python", "webrtc"],
                (32, 64),
                "high",
            ),
            (
                "DRM & entitlements",
                "Content protection and subscription entitlements.",
                ["python"],
                (16, 40),
                "high",
            ),
        ],
    },
    "government": {
        "Citizen Services": [
            (
                "Online application portal",
                "Multi-step citizen forms with eligibility checks.",
                ["typescript", "react", "python"],
                (24, 56),
                "high",
            ),
            (
                "Identity & eID integration",
                "National eID / verified identity onboarding.",
                ["python", "oauth"],
                (24, 56),
                "high",
            ),
            (
                "Appointment & case tracking",
                "Public-service appointments and case status.",
                ["python", "postgresql"],
                (16, 40),
                "medium",
            ),
        ],
        "Records & Compliance": [
            (
                "Public records registry",
                "Authoritative registry with versioning and search.",
                ["python", "postgresql", "elasticsearch"],
                (24, 64),
                "high",
            ),
            (
                "Accessibility & compliance",
                "WCAG 2.1 AA conformance and accessibility audit.",
                ["typescript"],
                (16, 36),
                "medium",
            ),
            (
                "Open-data publishing",
                "Datasets, APIs and transparency portals.",
                ["python", "fastapi"],
                (16, 40),
                "medium",
            ),
        ],
    },
}

SECTOR_TECH = {
    "finance": "ruby_on_rails",
    "ecommerce": "django",
    "healthcare": "python_fastapi",
    "industrial": "java_spring",
    "logistics": "go_gin",
    "education": "django",
    "media": "node_nestjs",
    "government": "java_spring",
}
SECTOR_COUNTRY = {
    "finance": "DE",
    "ecommerce": "ES",
    "healthcare": "FR",
    "industrial": "US",
    "logistics": "NL",
    "education": "GB",
    "media": "US",
    "government": "ES",
}
CLIENT_PREFIX = {
    "finance": ["NordBank", "PagoSeguro", "FinNova", "CapitalFlow"],
    "ecommerce": ["ShopHub", "MercadoVivo", "CartLabs", "TiendaUno"],
    "healthcare": ["SaludRed", "MediCore", "ClinicaPlus", "VitalCare"],
    "industrial": ["IndusTech", "FabricaSmart", "MaqExpert", "PlantaCore"],
    "logistics": ["CargoLink", "RutaViva", "FleetCore", "TransSmart"],
    "education": ["EduNova", "AulaViva", "LearnHub", "CampusOne"],
    "media": ["StreamLabs", "MediaForge", "PressVivo", "PlayCore"],
    "government": ["CivicaRed", "GobDigital", "PublicaOne", "EstadoSmart"],
}
ABBREV = {
    "Authentication & Access": "AUTH",
    "Data & Integrations": "DATA",
    "Frontend / UX": "FE",
    "Infrastructure & DevOps": "INFRA",
    "Security & Compliance": "SEC",
    "QA & Testing": "QA",
    "Project Management": "PM",
    "Analytics & Reporting": "ANL",
    "Notifications & Messaging": "NOTIF",
    "Search & Discovery": "SRCH",
    "Admin & Back-office": "ADMIN",
    "Integrations Platform": "INTG",
    "Payments & Billing": "PAY",
    "Regulatory Reporting": "REG",
    "Catalog & Search": "CAT",
    "Checkout & Cart": "CHK",
    "Clinical Records": "EHR",
    "Scheduling": "SCH",
    "Telemetry & IoT": "IOT",
    "Operations": "OPS",
    "Fleet & Routing": "FLEET",
    "Warehouse & Inventory": "WMS",
    "Courses & LMS": "LMS",
    "Assessments": "ASMT",
    "Content & Publishing": "CMS",
    "Streaming & Playback": "STRM",
    "Citizen Services": "CIT",
    "Records & Compliance": "REC",
}


def _modules_for(sector: str) -> dict[str, list[Template]]:
    """Common modules + the sector-specific ones."""
    return {**COMMON_MODULES, **SECTOR_MODULES[sector]}


def _build_project(rng: random.Random, index: int) -> dict:
    """Synthesise one historical project as a Budget dict (tasks = components)."""
    sector = rng.choice(list(SECTOR_MODULES))
    year = rng.choice([2022, 2023, 2024, 2025])
    catalog = _modules_for(sector)

    # Always include the sector-specific modules; sample a handful of common ones.
    sector_modules = list(SECTOR_MODULES[sector])
    common_pick = rng.sample(list(COMMON_MODULES), k=rng.randint(4, len(COMMON_MODULES)))
    chosen_modules = sector_modules + [m for m in common_pick if m not in sector_modules]

    components: list[dict] = []
    counters: dict[str, int] = {}
    for module in chosen_modules:
        templates = catalog[module]
        k = min(len(templates), rng.randint(2, 4))
        for name, desc, tech, (lo, hi), complexity in rng.sample(templates, k=k):
            counters[module] = counters.get(module, 0) + 1
            hours = rng.randint(lo, hi)
            tech_stack = rng.sample(tech, k=min(len(tech), rng.randint(1, len(tech))))
            components.append(
                {
                    "component_id": f"{ABBREV[module]}-{counters[module]:03d}",
                    "name": name,
                    "description": desc,
                    "module": module,
                    "tech_stack": tech_stack,
                    "estimated_hours": hours,
                    "complexity": complexity,
                    "dependencies": [],
                }
            )

    client = rng.choice(CLIENT_PREFIX[sector])
    summary_modules = ", ".join(chosen_modules[:3]).lower()
    return {
        "budget_id": f"TASK-{year}-{index:04d}",
        "client_metadata": {
            "name": f"{client} {rng.choice(['GmbH', 'S.L.', 'Inc.', 'SAS'])}",
            "sector": sector,
            "country": SECTOR_COUNTRY[sector],
        },
        "project_summary": f"{sector.capitalize()} platform covering {summary_modules}",
        "main_technology": SECTOR_TECH[sector],
        "year": year,
        "total_estimated_hours": sum(c["estimated_hours"] for c in components),
        "components": components,
    }


def generate_corpus(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[dict]:
    """Generate ``count`` historical projects, deterministically from ``seed``.

    Each project is a Budget dict whose components are module-tagged tasks. The
    output validates against the :class:`Budget` Pydantic schema, so it can be
    POSTed to ``/embeddings/ingest`` unchanged.
    """
    rng = random.Random(seed)
    return [_build_project(rng, i + 1) for i in range(count)]


# --- ingest ---------------------------------------------------------------


def _resolve_base_url(client: httpx.Client) -> str:
    explicit = os.environ.get("ESTIMATOR_BASE_URL")
    for base_url in (explicit,) if explicit else CANDIDATE_BASE_URLS:
        try:
            if client.get(f"{base_url}/health").status_code == 200:
                return base_url
        except httpx.TransportError:
            continue
    print(
        "ERROR: no estimator API reachable. Start the stack (docker compose up -d) "
        "or set ESTIMATOR_BASE_URL.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _service_headers() -> dict[str, str]:
    """``X-Service-Token`` when the Session 15 guard is on, ``{}`` otherwise.

    ``app/api/service_token.py`` covers every route except ``/health`` (which is
    why the base-url probe still succeeds when the token is missing) — so without
    this header the first ingest POST 401s and the run dies at the first project.
    An unset ``AI_SERVICE_TOKEN`` disables the middleware, so an empty dict is the
    correct payload there, not a blank header.
    """
    token = os.environ.get("AI_SERVICE_TOKEN")
    return {"X-Service-Token": token} if token else {}


def ingest_corpus(
    corpus: list[dict],
    base_url: str | None = None,
    *,
    document_type: str = DOCUMENT_TYPE,
    chunk_type: str = CHUNK_TYPE,
    source_prefix: str = "data/task_corpus.json",
) -> None:
    """One document per project; 409 means already ingested (idempotent).

    The keyword arguments let a sibling corpus (see ``build_clinic_corpus.py``)
    reuse this loop while landing under its own ``document_type`` — which is what
    makes that corpus independently droppable — without touching this one.
    """
    headers = _service_headers()
    with httpx.Client(timeout=120.0) as client:
        base_url = base_url or _resolve_base_url(client)
        created, skipped = 0, 0
        for project in corpus:
            response = client.post(
                f"{base_url}/embeddings/ingest",
                headers=headers,
                json={
                    "source_path": f"{source_prefix}::{project['budget_id']}",
                    "document_type": document_type,
                    "chunk_type": chunk_type,
                    "content": project,
                },
            )
            if response.status_code == 200:
                created += 1
            elif response.status_code == 409:
                skipped += 1
            else:
                print(
                    f"ERROR ingesting {project['budget_id']}: "
                    f"{response.status_code} {response.text[:200]}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
        tasks = sum(len(p["components"]) for p in corpus)
        print(
            f"Task corpus: {len(corpus)} projects / {tasks} tasks — "
            f"{created} ingested, {skipped} already present (chunk_type={chunk_type})."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and ingest the task-granular corpus.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="Number of projects.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed (reproducible).")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Output JSON path.")
    parser.add_argument("--base-url", default=None, help="Estimator base URL (else auto-probe).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate-only", action="store_true", help="Write JSON, do not ingest.")
    mode.add_argument("--ingest-only", action="store_true", help="Ingest an existing JSON file.")
    parser.add_argument("--ingest", action="store_true", help="Generate AND ingest (default flow).")
    args = parser.parse_args()

    if args.ingest_only:
        corpus = json.loads(args.out.read_text())
        print(f"Loaded {len(corpus)} projects from {args.out}.")
    else:
        corpus = generate_corpus(count=args.count, seed=args.seed)
        args.out.write_text(json.dumps(corpus, indent=2, ensure_ascii=False))
        tasks = sum(len(p["components"]) for p in corpus)
        print(f"Wrote {len(corpus)} projects / {tasks} tasks → {args.out}")

    if args.generate_only:
        return
    if args.ingest or args.ingest_only:
        ingest_corpus(corpus, base_url=args.base_url)
    else:
        print("(generation only; pass --ingest to load into pgvector)")


if __name__ == "__main__":
    main()
