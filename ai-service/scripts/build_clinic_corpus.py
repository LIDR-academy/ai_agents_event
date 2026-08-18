#!/usr/bin/env python3
"""Generate and ingest a CLINIC-domain task corpus, additively (demo coverage).

Why this exists, in one paragraph. The per-task hours step
(``app/generation/rag/task_hours.py``) grounds each task by nearest-neighbour
search over ``chunk_type='historical_task'`` chunks. Coverage therefore depends on
how many *distinct template texts* the corpus holds near the tasks the structure
agent emits — NOT on how many projects it holds: the base corpus replicates the
same ~44 templates across 60 projects, so a task's five nearest neighbours are
routinely the same template five times over, separated by ~0.006 of cosine
distance. Adding projects moves nothing. Adding templates does.

Two more design constraints came out of measuring the real runs:

* **Hour ranges must be tight.** ``reliability = weighted_similarity ×
  (1 − dispersion)``, and the base generator draws ``rng.randint(24, 56)`` — five
  draws from a 2.3× span give a coefficient of variation ≈ 0.20 that caps
  reliability below the green band no matter how close the match. Every template
  here is authored with ``hi/lo <= 1.25`` (asserted in the tests), except three
  deliberately wide ones so amber rows still exist.
* **Descriptions carry Spanish keywords.** The transcripts are Spanish and
  ``text-embedding-3-small`` is only weakly cross-lingual; seeding the domain terms
  costs nothing and hedges against a run that names its tasks in Spanish.

This corpus lands under its own ``document_type`` so it is independently
droppable, while keeping ``chunk_type='historical_task'`` so the hours search
picks it up with no code change. The base corpus is never read or written.

Usage (inside the container)::

    docker compose exec ai-service python scripts/build_clinic_corpus.py --generate-only
    docker compose exec ai-service python scripts/build_clinic_corpus.py --ingest

Drop it again — one statement, ``ON DELETE CASCADE`` takes the chunks::

    DELETE FROM documents WHERE document_type = 'clinic_task_breakdown';
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_task_corpus import COMMON_MODULES, Template, ingest_corpus  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "clinic_task_corpus.json"
DOCUMENT_TYPE = "clinic_task_breakdown"
CHUNK_TYPE = "historical_task"
SOURCE_PREFIX = "data/clinic_task_corpus.json"
DEFAULT_SEED = 41
DEFAULT_COUNT = 35

# ---------------------------------------------------------------------------
# Group A — clinic domain. Every module here is traceable to a task the demo
# transcript's structure produced and the base corpus could not ground.
# ---------------------------------------------------------------------------

CLINIC_MODULES: dict[str, list[Template]] = {
    "Appointments & Agenda": [
        ("Unified multi-centre appointment agenda",
         "One booking calendar spanning a network of clinics, with per-centre calendars and "
         "central occupancy visibility (agenda unificada, red de centros, ocupación).",
         ["python", "postgresql"], (44, 54), "high"),
        ("Appointment types & scheduling rules engine",
         "Per-appointment-type durations, professional eligibility, emergency slot reservations "
         "and weekday overload rules applied automatically (tipos de cita, motor de reglas).",
         ["python", "postgresql"], (40, 48), "high"),
        ("Booking engine with slot locking",
         "Concurrent-safe slot reservation with pessimistic locking so two agents cannot take "
         "the same slot (motor de reserva, bloqueo de huecos, concurrencia).",
         ["python", "postgresql"], (34, 42), "high"),
        ("Shared resource & room allocation",
         "Allocation of consulting rooms and shared equipment that rotates between centres "
         "(salas, recursos compartidos, equipos rotatorios).",
         ["python", "postgresql"], (32, 38), "high"),
        ("Change, reschedule & cancellation workflows",
         "Reschedule and cancellation flows with policy windows, audit and downstream "
         "propagation (reprogramación, anulación, flujos de cambio).",
         ["python", "postgresql"], (28, 34), "medium"),
        ("Waiting list & no-show handling",
         "Priority queue that reallocates freed slots, plus no-show marking and absenteeism "
         "tracking (lista de espera, absentismo, no presentados).",
         ["python", "celery"], (26, 32), "medium"),
        ("Agenda notification hooks",
         "Domain events emitted on booking, change and cancellation for the notification "
         "platform to consume (eventos de agenda, avisos de cita).",
         ["python", "redis"], (18, 22), "medium"),
        ("Agenda performance & scaling",
         "Query tuning, caching and load testing of the calendar under multi-centre "
         "concurrency (rendimiento, escalado, carga).",
         ["python", "postgresql"], (24, 30), "high"),
    ],
    "Patient Portal": [
        ("Patient profile & dependents management",
         "Patient account with linked dependants and delegated access for carers "
         "(perfil del paciente, dependientes, acceso delegado).",
         ["typescript", "react"], (28, 34), "medium"),
        ("Test results viewer",
         "Patient-facing viewer for laboratory results and discharge reports with download "
         "and read receipts (visor de resultados, analíticas, informes de alta).",
         ["typescript", "react"], (26, 32), "medium"),
        ("Partial medical history exposure",
         "Curated read-only view of active medication, upcoming appointments and reports, "
         "deliberately excluding sensitive clinical detail (historial parcial, medicación activa).",
         ["python", "postgresql"], (30, 36), "high"),
        ("Self-service booking journey",
         "End-to-end patient booking flow across web and mobile with specialty and centre "
         "selection (reserva online, portal del paciente).",
         ["typescript", "react"], (32, 38), "high"),
        ("Payment within the booking flow",
         "Co-payment collected inline before or after the appointment, tied to the booking "
         "(copago, pago en la reserva, tarjeta).",
         ["typescript", "stripe"], (24, 30), "medium"),
        ("Accessibility & large-type patient UI",
         "WCAG-conformant interface tuned for elderly and low-vision patients: large type, "
         "contrast, screen-reader flows (accesibilidad, letra grande, pacientes mayores).",
         ["typescript", "react"], (26, 32), "high"),
        ("Multi-language patient experience",
         "Locale routing and translated content across the portal and app "
         "(multi-idioma, castellano, catalán, inglés).",
         ["typescript", "i18next"], (20, 25), "medium"),
    ],
    "Teleconsultation": [
        ("Embedded video consultation",
         "In-product video call launched from the appointment, no external tool hop "
         "(videoconsulta integrada, teleconsulta, videollamada).",
         ["typescript", "webrtc"], (40, 48), "high"),
        ("Signalling, STUN and TURN infrastructure",
         "Signalling server plus STUN/TURN relays and NAT traversal for reliable calls "
         "(señalización, servidores TURN, atravesado de NAT).",
         ["typescript", "webrtc"], (30, 36), "high"),
        ("In-call document & image sharing",
         "Share a document or an image with the patient during the live consultation "
         "(compartir documento, imagen en consulta).",
         ["typescript", "webrtc"], (22, 27), "medium"),
        ("Consultation recording policy & controls",
         "Explicit no-recording enforcement with legal-hold exceptions and a consent notice "
         "(política de grabación, requisitos legales).",
         ["python", "postgresql"], (16, 20), "medium"),
        ("Teleconsultation billing events",
         "Proof-of-consultation records emitted so a remote visit can be invoiced like a "
         "presential one (registro de consulta, facturación de teleconsulta).",
         ["python", "postgresql"], (18, 22), "medium"),
    ],
    "Clinical Interoperability": [
        ("ePrescription integration (FHIR)",
         "Certified integration with a regional electronic-prescription service over FHIR, "
         "including its certification cycle (receta electrónica, FHIR, certificación).",
         ["python", "fhir"], (52, 64), "high"),
        ("Regional health-system gateway (HL7 v2 + FHIR)",
         "Gateway speaking both the legacy HL7 v2 endpoints and the newer FHIR ones of a "
         "regional public health system (sistema autonómico, HL7 v2, pasarela).",
         ["python", "hl7"], (48, 58), "high"),
        ("HIS synchronisation with a third-party vendor",
         "Two-way sync of visits and clinical notes with a purchased hospital information "
         "system over its vendor API (sincronización con el HIS, proveedor externo).",
         ["python", "hl7"], (46, 56), "high"),
        ("External laboratory results ingestion",
         "Automated intake of analytics results from an external laboratory, replacing manual "
         "PDF upload (resultados de laboratorio, laboratorio externo, ingesta automática).",
         ["python", "hl7"], (38, 46), "high"),
        ("Patient identity & master patient index",
         "Cross-system patient matching and a master patient index with deduplication rules "
         "(identidad del paciente, índice maestro, MPI, deduplicación).",
         ["python", "postgresql"], (40, 48), "high"),
        ("Clinical terminology services",
         "SNOMED CT, LOINC and ICD-10 code-system mapping with a fallback for unmapped codes "
         "(servicios de terminología, codificación clínica, catálogos).",
         ["python", "postgresql"], (32, 38), "high"),
        ("Consent-aware clinical data exchange",
         "Enforce patient consent and purpose limitation on every outbound clinical exchange "
         "(intercambio con consentimiento, limitación de finalidad).",
         ["python", "fhir"], (30, 36), "high"),
        ("Interface monitoring & alerting",
         "Dashboards and alerts per external interface, with retry and dead-letter handling "
         "(monitorización de interfaces, reintentos, alertas).",
         ["python", "prometheus"], (24, 30), "medium"),
        ("Integration API documentation & contract tests",
         "Published interface specs plus contract tests against mocked FHIR/HL7 peers so work "
         "proceeds without the real sandbox (documentación de API, pruebas de contrato).",
         ["python", "pytest"], (22, 27), "medium"),
    ],
    "Data Migration & Cutover": [
        ("Legacy system discovery & data access",
         "Obtain and profile access to an on-premise legacy scheduler database across sites "
         "(descubrimiento, acceso a datos, base de datos heredada).",
         ["python", "sqlserver"], (24, 30), "medium"),
        ("Migration scope & business rules",
         "Agree which historical records migrate, retention rules and exclusions "
         "(alcance de la migración, reglas de negocio, retención).",
         ["python", "postgresql"], (20, 25), "medium"),
        ("Field mapping & transformation rules",
         "Source-to-target field mapping with transformation and defaulting rules per entity "
         "(mapeo de campos, transformación, correspondencias).",
         ["python", "sqlserver"], (34, 42), "high"),
        ("Data cleansing & deduplication",
         "Clean, normalise and deduplicate years of legacy records before load "
         "(depuración de datos, limpieza, deduplicación).",
         ["python", "pandas"], (32, 38), "high"),
        ("Migration validation & rehearsals",
         "Repeatable dry-run loads with reconciliation reports and sign-off criteria "
         "(validación, ensayos de migración, conciliación).",
         ["python", "postgresql"], (30, 36), "high"),
        ("Cutover plan & parallel-run coexistence",
         "Cutover runbook, freeze windows and dual-run coexistence while the legacy system is "
         "retired (plan de cutover, convivencia, ventana de corte).",
         ["python", "postgresql"], (28, 34), "high"),
        ("Post-cutover support & reconciliation",
         "Hypercare period with daily reconciliation and a rollback path "
         "(soporte post-arranque, conciliación diaria, vuelta atrás).",
         ["python", "postgresql"], (24, 30), "medium"),
        ("Migration communication & user training",
         "Stakeholder communication plan and training for administrative staff through the "
         "switchover (comunicación, formación de usuarios, gestión del cambio).",
         ["confluence"], (18, 22), "low"),
    ],
    "Health Data Compliance": [
        ("Clinical access audit trail",
         "Immutable who-read-which-record log over clinical data, queryable for inspections "
         "(traza de accesos, quién ha visto qué, auditoría clínica).",
         ["python", "postgresql"], (32, 38), "high"),
        ("Patient consent management",
         "Per-purpose consent capture, withdrawal and enforcement at query time "
         "(gestión de consentimientos, retirada del consentimiento).",
         ["python", "postgresql"], (30, 36), "high"),
        ("Special-category data encryption",
         "Column-level encryption at rest plus enforced TLS in transit for health data "
         "(cifrado en reposo y en tránsito, datos de categoría especial).",
         ["python", "postgresql"], (26, 32), "high"),
        ("GDPR programme & records of processing",
         "Processing register, DPIA and legal-basis documentation for health data "
         "(programa RGPD, registro de actividades, evaluación de impacto).",
         ["confluence"], (28, 34), "high"),
        ("Fine-grained clinical role model",
         "Role and purpose based access control distinguishing clinician, front-desk, admin "
         "and patient (control de acceso por rol, roles clínicos).",
         ["python", "postgresql"], (30, 36), "high"),
        ("Security incident response runbook",
         "Detection, notification and 72-hour breach reporting procedure with drills "
         "(respuesta a incidentes, notificación de brechas).",
         ["confluence"], (20, 25), "medium"),
        ("Third-party processor compliance",
         "Data-processing agreements and security review of every external system in the "
         "clinical chain (cumplimiento de terceros, encargados del tratamiento).",
         ["confluence"], (18, 22), "medium"),
    ],
    "Field Work & Offline Sync": [
        ("Offline mobile technology selection & spike",
         "Evaluate offline-capable mobile stacks and prove the sync approach with a spike "
         "(selección tecnológica, prueba de concepto, app offline).",
         ["typescript", "react_native"], (24, 30), "high"),
        ("Encrypted on-device data store",
         "Local encrypted database holding the day's route and patient data with no "
         "connectivity (almacén local cifrado, sin conexión, datos del paciente).",
         ["typescript", "sqlite"], (36, 44), "high"),
        ("Bidirectional sync engine",
         "Delta synchronisation of records captured offline once connectivity returns "
         "(motor de sincronización, sincronización bidireccional).",
         ["typescript", "react_native"], (44, 54), "high"),
        ("Sync conflict detection & resolution",
         "Conflict rules for concurrent edits of the same record, with a reviewable conflict "
         "queue (resolución de conflictos, edición concurrente).",
         ["typescript", "react_native"], (34, 42), "high"),
        ("Offline authentication & access",
         "Credential caching, offline session lifetime and re-authentication on reconnect "
         "(autenticación sin conexión, sesión offline).",
         ["typescript", "react_native"], (26, 32), "high"),
        ("Mobile device security hardening",
         "Device enrolment, remote wipe, screen-lock enforcement and jailbreak detection "
         "(seguridad del dispositivo, borrado remoto).",
         ["typescript", "mdm"], (24, 30), "high"),
        ("Home-visit management UI",
         "Daily route list, visit detail and on-site signature capture for nurses "
         "(gestión de visitas, ruta del día, firma, enfermería a domicilio).",
         ["typescript", "react_native"], (32, 38), "medium"),
        ("Clinical workflow & offline forms",
         "Structured care forms completed offline with validation and required-field rules "
         "(formularios offline, flujo de trabajo clínico).",
         ["typescript", "react_native"], (28, 34), "medium"),
        ("Offline resilience & sync auditing",
         "Retry, backoff, partial-failure recovery and an audit log of every sync outcome "
         "(resiliencia, reintentos, auditoría de sincronización).",
         ["typescript", "react_native"], (26, 32), "high"),
    ],
    "Clinical Billing & Payments": [
        ("Insurer and mutual tariff model",
         "Tariffs, authorisations and co-payment rules per insurer and private policy "
         "(mutuas, seguros privados, tarifas, autorizaciones, copago).",
         ["python", "postgresql"], (38, 46), "high"),
        ("Online payment flows",
         "Card payment for co-payments through a standard gateway, before or after the visit "
         "(pasarela de pago, Redsys, Stripe, pago con tarjeta).",
         ["python", "stripe"], (30, 36), "high"),
        ("ERP bridge for financial posting",
         "Push medical acts and collected payments into the corporate ERP where accounting "
         "lives (volcado al ERP, Business Central, contabilidad).",
         ["python", "dynamics365"], (40, 48), "high"),
        ("Invoicing & receipts",
         "Invoice and receipt generation with fiscal numbering and patient delivery "
         "(facturación, recibos, numeración fiscal).",
         ["python", "postgresql"], (28, 34), "medium"),
        ("Refunds, fees & adjustments",
         "Refund and adjustment flows with reason codes reconciled against the gateway "
         "(devoluciones, ajustes, conciliación).",
         ["python", "stripe"], (22, 27), "medium"),
        ("Chargeback & dispute handling",
         "Dispute intake, evidence submission and settlement reconciliation "
         "(contracargos, disputas, liquidación).",
         ["python", "stripe"], (20, 25), "medium"),
        ("Medical act capture for billing",
         "Record what was performed in each visit so it can be priced and invoiced "
         "(actos médicos, registro de la visita, facturación).",
         ["python", "postgresql"], (26, 32), "medium"),
    ],
    "Clinical Analytics & Reporting": [
        ("Management KPI definition",
         "Agree the operational indicator set with the executive team before building "
         "(definición de KPI, indicadores de dirección).",
         ["confluence"], (16, 20), "low"),
        ("Occupancy & waiting-time dashboards",
         "Self-refreshing dashboards for occupancy per centre and per professional, and "
         "waiting times (ocupación por centro, tiempos de espera, cuadro de mando).",
         ["python", "metabase"], (30, 36), "medium"),
        ("Absenteeism & no-show analytics",
         "No-show rate by centre, professional and appointment type, with trend analysis "
         "(tasa de absentismo, no presentados, tendencias).",
         ["python", "metabase"], (22, 27), "medium"),
        ("Revenue by service-line reporting",
         "Income broken down by medical act type and payer (ingresos por tipo de acto, pagador).",
         ["python", "metabase"], (24, 30), "medium"),
        ("Regulatory reporting to health authorities",
         "Activity statistics and mandatory registries in the official formats, which change "
         "periodically (reporting regulatorio, administración sanitaria, formatos oficiales).",
         ["python", "postgresql"], (34, 42), "high"),
        ("Analytics data model & refresh pipeline",
         "Reporting schema and scheduled refresh decoupled from the transactional database "
         "(modelo analítico, refresco programado).",
         ["python", "dbt"], (28, 34), "high"),
    ],
}

# ---------------------------------------------------------------------------
# Group B — generic engineering at the granularity the structure agent emits.
# The base COMMON_MODULES stop one level of abstraction too high ("Automated
# test suite"); the agent writes "Test data" and "Standards conformance".
# ---------------------------------------------------------------------------

ENGINEERING_MODULES: dict[str, list[Template]] = {
    "Notifications Platform": [
        ("SMS, email and push provider setup",
         "Provider accounts, sender identities, deliverability and per-channel fallback "
         "(alta de proveedores, SMS, email, push, entregabilidad).",
         ["python", "twilio"], (22, 27), "medium"),
        ("Notification templating & i18n",
         "Versioned message templates rendered per locale and channel "
         "(plantillas de mensaje, multi-idioma, localización).",
         ["python", "jinja2"], (20, 25), "medium"),
        ("Recipient notification preferences",
         "Per-user channel preferences, quiet hours and opt-out honoured at send time "
         "(preferencias de notificación, baja, horarios).",
         ["python", "postgresql"], (18, 22), "medium"),
        ("Secure tokenised links",
         "Signed expiring links so a message can point at private content without a login "
         "(enlaces seguros, tokens firmados, caducidad).",
         ["python", "redis"], (16, 20), "high"),
        ("Appointment reminder scheduling",
         "Reminder cadence per appointment with cancellation on reschedule "
         "(recordatorios de cita, programación de avisos).",
         ["python", "celery"], (22, 27), "medium"),
        ("Operator tooling & delivery console",
         "Back-office view of what was sent, to whom, delivery state and manual resend "
         "(herramientas de operador, consola de envíos, reenvío).",
         ["typescript", "react"], (24, 30), "medium"),
    ],
    "Delivery & Change Enablement": [
        ("Risk, assumption and RAID tracking",
         "Live RAID log with owners and mitigation for the delivery's known unknowns "
         "(riesgos, supuestos, RAID, mitigación).",
         ["confluence"], (16, 20), "low"),
        ("End-user training & help centre",
         "Role-based training material and a searchable help centre for staff "
         "(formación, centro de ayuda, material por rol).",
         ["confluence"], (24, 30), "low"),
        ("Stakeholder communication plan",
         "Cadence, audiences and artefacts for keeping a multi-site organisation informed "
         "(plan de comunicación, interlocutores).",
         ["confluence"], (14, 17), "low"),
        ("Phased rollout & site onboarding",
         "Per-centre rollout sequencing with entry criteria and a pilot site "
         "(despliegue por fases, centro piloto, incorporación).",
         ["confluence"], (22, 27), "medium"),
        ("Requirements discovery workshops",
         "Structured discovery sessions turning stakeholder conversations into scoped "
         "requirements (talleres de descubrimiento, toma de requisitos).",
         ["confluence"], (24, 60), "medium"),
    ],
    "QA Depth": [
        ("Test data management",
         "Realistic anonymised datasets and per-environment refresh for repeatable tests "
         "(datos de prueba, anonimización, entornos).",
         ["python", "pytest"], (22, 27), "medium"),
        ("Offline and connectivity-loss testing",
         "Scenarios covering flight-mode capture, partial sync and reconnection "
         "(pruebas offline, pérdida de conectividad).",
         ["typescript", "detox"], (24, 30), "high"),
        ("Standards conformance testing",
         "Conformance suites against the external standards the system claims to speak "
         "(conformidad con estándares, certificación).",
         ["python", "pytest"], (26, 32), "high"),
        ("Accessibility audit & remediation",
         "Automated and manual accessibility passes with a remediation backlog "
         "(auditoría de accesibilidad, remediación).",
         ["typescript", "axe"], (20, 25), "medium"),
        ("Mobile app store release process",
         "Signing, store listings, staged rollout and release notes for both stores "
         "(publicación en tiendas, despliegue escalonado).",
         ["typescript", "fastlane"], (18, 22), "medium"),
        ("User acceptance testing coordination",
         "UAT plan, scripted scenarios and defect triage with the client "
         "(pruebas de aceptación, triaje de incidencias).",
         ["confluence"], (20, 48), "medium"),
    ],
    "API Foundations": [
        ("Backend service bootstrap",
         "Project skeleton, dependency wiring, layered configuration and the first health "
         "endpoint (arranque del servicio, esqueleto del proyecto).",
         ["python", "fastapi"], (18, 22), "medium"),
        ("Routing, namespacing & API versioning",
         "URL namespaces, versioning policy and deprecation headers "
         "(rutas, espacios de nombres, versionado de API).",
         ["python", "fastapi"], (14, 17), "medium"),
        ("Global error handling & problem responses",
         "Centralised exception handling emitting a consistent problem envelope with a "
         "correlation id (manejo global de errores, identificador de correlación).",
         ["python", "fastapi"], (16, 20), "medium"),
        ("Pagination, filtering & response shaping",
         "Cursor pagination, filter and sort parameters and per-endpoint serialisation "
         "(paginación, filtrado, forma de la respuesta).",
         ["python", "postgresql"], (18, 22), "medium"),
        ("Background job runner & scheduling",
         "Worker process, queues, retries and scheduled jobs "
         "(trabajos en segundo plano, colas, tareas programadas).",
         ["python", "celery"], (22, 27), "medium"),
        ("Core schema, indexes & migrations",
         "Entity model, reversible migration chain, constraints and index tuning "
         "(modelo de datos, migraciones, índices).",
         ["postgresql"], (28, 34), "high"),
        ("Multi-centre tenancy scoping",
         "Row-level scoping so each site sees only its own data, enforced in the data layer "
         "(multi-centro, aislamiento de datos, ámbito por centro).",
         ["postgresql"], (26, 32), "high"),
    ],
    "Identity & Session Hardening": [
        ("Token issuance & key publication",
         "Asymmetric token signing with a rotating key set published for verifiers "
         "(emisión de tokens, rotación de claves, JWKS).",
         ["python", "oauth2"], (24, 30), "high"),
        ("Refresh token rotation & revocation",
         "Rotating refresh tokens with reuse detection and server-side revocation "
         "(rotación de tokens, revocación, detección de reutilización).",
         ["python", "redis"], (22, 27), "high"),
        ("Session lifecycle management",
         "Session creation, idle and absolute timeouts, and device listing "
         "(gestión de sesiones, caducidad, dispositivos).",
         ["python", "redis"], (18, 22), "medium"),
        ("Multi-factor authentication for staff",
         "TOTP enrolment, recovery codes and step-up authentication on sensitive actions "
         "(doble factor, códigos de recuperación, verificación adicional).",
         ["python", "totp"], (24, 30), "high"),
        ("Identity provider / SSO federation",
         "Federate staff login with the corporate identity provider "
         "(federación de identidad, inicio de sesión único).",
         ["python", "oauth2"], (22, 27), "high"),
        ("Brute-force & abuse protections",
         "Login throttling, lockout policy and anomaly alerts "
         "(protección contra fuerza bruta, bloqueo de cuenta).",
         ["python", "redis"], (16, 20), "medium"),
    ],
}

CLINIC_ABBREV = {
    # Group A
    "Appointments & Agenda": "AGND",
    "Patient Portal": "PORT",
    "Teleconsultation": "TELE",
    "Clinical Interoperability": "INTEROP",
    "Data Migration & Cutover": "MIGR",
    "Health Data Compliance": "HGDPR",
    "Field Work & Offline Sync": "OFFL",
    "Clinical Billing & Payments": "CBIL",
    "Clinical Analytics & Reporting": "ANLR",
    # Group B
    "Notifications Platform": "NOTP",
    "Delivery & Change Enablement": "CHNG",
    "QA Depth": "QAD",
    "API Foundations": "APIF",
    "Identity & Session Hardening": "IDSH",
    # Imported base modules — same codes as build_task_corpus.ABBREV so a chunk's
    # component_id stays readable across both corpora.
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
}

# The project header is ~40% of a short chunk's tokens and is identical across every
# chunk of a project — long enough to place the domain, short enough that tasks within
# one project still separate from each other.
PROJECT_SUMMARIES = {
    "portal": "Clinic network patient platform: web and mobile portal, unified multi-centre "
              "appointment booking, teleconsultation and appointment reminders",
    "interop": "Clinic network interoperability programme: FHIR and HL7 integration with the "
               "regional health system, hospital information system sync, external laboratory "
               "results and electronic prescription",
    "compliance": "Clinic network health-data compliance platform: special-category data "
                  "handling, clinical access audit trail, consent management and regulatory "
                  "reporting",
    "mobility": "Clinic network field-work platform: offline nursing home-visit application "
                "with bidirectional sync, route planning and on-site clinical note capture",
    "billing": "Clinic network revenue platform: clinical billing, insurer tariffs, ERP "
               "financial posting and online patient payments",
    "analytics": "Clinic network management platform: occupancy and waiting-time dashboards, "
                 "absenteeism analytics and regulatory reporting across centres",
    "migration": "Clinic network scheduling replacement: legacy on-premise appointment system "
                 "migration, unified agenda rollout and multi-site cutover",
    "portal_billing": "Private clinic platform: patient portal with online co-payment, "
                      "appointment booking, invoicing and ERP financial posting",
    "interop_mobility": "Clinic network clinical platform: hospital system interoperability, "
                        "electronic prescription and an offline application for home nursing "
                        "visits",
    "compliance_analytics": "Regional healthcare provider platform: consent and access "
                            "governance over clinical data, plus operational and regulatory "
                            "reporting",
}

# Which clinic modules a theme pulls toward. Sampling is biased, not forced, so the
# same module still appears across differently-themed projects.
THEME_BIAS = {
    "portal": ["Patient Portal", "Appointments & Agenda", "Teleconsultation"],
    "interop": ["Clinical Interoperability", "Health Data Compliance"],
    "compliance": ["Health Data Compliance", "Clinical Interoperability"],
    "mobility": ["Field Work & Offline Sync", "Appointments & Agenda"],
    "billing": ["Clinical Billing & Payments", "Patient Portal"],
    "analytics": ["Clinical Analytics & Reporting", "Appointments & Agenda"],
    "migration": ["Data Migration & Cutover", "Appointments & Agenda"],
    "portal_billing": ["Patient Portal", "Clinical Billing & Payments"],
    "interop_mobility": ["Clinical Interoperability", "Field Work & Offline Sync"],
    "compliance_analytics": ["Health Data Compliance", "Clinical Analytics & Reporting"],
}

CLINIC_CLIENTS = [
    "SaludRed", "MediCore", "ClinicaPlus", "VitalCare",
    "RedClinicas", "GrupoSalud", "CentroMedico", "PoliclinicaNorte",
]
CLINIC_TECH = ["python_fastapi", "ruby_on_rails", "node_nestjs", "django", "java_spring"]
CLINIC_COUNTRIES = ["ES", "ES", "ES", "PT", "FR"]
CLINIC_SUFFIXES = ["S.L.", "S.A.", "Group", "Salud"]
YEARS = [2022, 2023, 2024, 2025]

SECTOR = "healthcare"


def _build_clinic_project(rng: random.Random, index: int) -> dict:
    """Synthesise one historical clinic project as a Budget dict (tasks = components)."""
    theme = rng.choice(list(PROJECT_SUMMARIES))
    year = rng.choice(YEARS)

    biased = [m for m in THEME_BIAS[theme] if m in CLINIC_MODULES]
    rest = [m for m in CLINIC_MODULES if m not in biased]
    clinic_pick = biased + rng.sample(rest, k=rng.randint(3, 4))
    engineering_pick = rng.sample(list(ENGINEERING_MODULES), k=rng.randint(3, 4))
    legacy_pick = rng.sample(list(COMMON_MODULES), k=rng.randint(2, 3))

    catalog: dict[str, list[Template]] = {
        **COMMON_MODULES,
        **ENGINEERING_MODULES,
        **CLINIC_MODULES,
    }
    chosen = clinic_pick + engineering_pick + [m for m in legacy_pick if m not in clinic_pick]

    components: list[dict] = []
    counters: dict[str, int] = {}
    for module in chosen:
        templates = catalog[module]
        k = min(len(templates), rng.randint(3, 5))
        for name, desc, tech, (lo, hi), complexity in rng.sample(templates, k=k):
            counters[module] = counters.get(module, 0) + 1
            components.append(
                {
                    "component_id": f"{CLINIC_ABBREV[module]}-{counters[module]:03d}",
                    "name": name,
                    "description": desc,
                    "module": module,
                    "tech_stack": rng.sample(tech, k=min(len(tech), rng.randint(1, len(tech)))),
                    "estimated_hours": rng.randint(lo, hi),
                    "complexity": complexity,
                    "dependencies": [],
                }
            )

    client = rng.choice(CLINIC_CLIENTS)
    return {
        "budget_id": f"CLIN-{year}-{index:04d}",
        "client_metadata": {
            "name": f"{client} {rng.choice(CLINIC_SUFFIXES)}",
            "sector": SECTOR,
            "country": rng.choice(CLINIC_COUNTRIES),
        },
        "project_summary": PROJECT_SUMMARIES[theme],
        "main_technology": rng.choice(CLINIC_TECH),
        "year": year,
        "total_estimated_hours": sum(c["estimated_hours"] for c in components),
        "components": components,
    }


def generate_corpus(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[dict]:
    """Generate ``count`` clinic projects, deterministically from ``seed``."""
    rng = random.Random(seed)
    return [_build_clinic_project(rng, i + 1) for i in range(count)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and ingest the clinic task corpus.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="Number of projects.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed (reproducible).")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Output JSON path.")
    parser.add_argument("--base-url", default=None, help="AI service base URL (else auto-probe).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate-only", action="store_true", help="Write JSON, do not ingest.")
    mode.add_argument("--ingest-only", action="store_true", help="Ingest an existing JSON file.")
    parser.add_argument("--ingest", action="store_true", help="Generate AND ingest.")
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
        ingest_corpus(
            corpus,
            base_url=args.base_url,
            document_type=DOCUMENT_TYPE,
            chunk_type=CHUNK_TYPE,
            source_prefix=SOURCE_PREFIX,
        )
    else:
        print("(generation only; pass --ingest to load into pgvector)")


if __name__ == "__main__":
    main()
