"""Reference retrieval stub — a SAFETY NET for the Session 12 exercise.

Use this only if your Session 9/10 retrieval pipeline is not ready or you want to
debug the agent LOOP without bringing up the database. It returns canned historical
budget items keyed on simple keyword matching, in the SAME shape the real
``search_budgets`` backend returns::

    {"id": int, "content_preview": str, "sector": str,
     "budget_id": str, "estimated_hours": float, "distance": float}

The ideal solution WRAPS your real pipeline instead of this. To plug the stub into
the reference agent, run the demo script with ``--stub``::

    uv run python scripts/run_agent_s12.py \\
        exercises/session-12/sample_transcript_simple.txt --model gpt-5-mini --stub

This file has NO dependency on the app package on purpose — it is standalone.
"""

from __future__ import annotations

from typing import Any

# A tiny fake corpus. Each entry: the keywords that should match it, plus the
# historical item to return (with recorded engineer-hours). Numbers are illustrative.
_CORPUS: list[dict[str, Any]] = [
    {
        "keywords": ("oauth", "auth", "autenticación", "jwt", "login", "token", "sso"),
        "id": 1001,
        "content_preview": "Backend de autenticación OAuth2 con JWT, refresh tokens y multi-tenant.",
        "sector": "finance",
        "budget_id": "BUD-AUTH-2023-07",
        "estimated_hours": 420.0,
    },
    {
        "keywords": ("oauth", "auth", "rate limiting", "seguridad", "psd2"),
        "id": 1002,
        "content_preview": "Módulo de seguridad: rate limiting por cliente, SCA y gestión de consentimientos.",
        "sector": "finance",
        "budget_id": "BUD-AUTH-2022-11",
        "estimated_hours": 380.0,
    },
    {
        "keywords": ("backend", "api", "pedidos", "orders", "rutas", "tarifas", "núcleo", "core"),
        "id": 2001,
        "content_preview": "Backend de negocio: gestión de pedidos, asignación de rutas y cálculo de tarifas con API REST.",
        "sector": "logistics",
        "budget_id": "BUD-CORE-2023-02",
        "estimated_hours": 1150.0,
    },
    {
        "keywords": ("backend", "api", "seguimiento", "tracking", "envíos"),
        "id": 2002,
        "content_preview": "Servicio de seguimiento de envíos en tiempo real con API para terceros.",
        "sector": "logistics",
        "budget_id": "BUD-CORE-2022-09",
        "estimated_hours": 940.0,
    },
    {
        "keywords": (
            "erp",
            "sap",
            "integración",
            "integration",
            "idoc",
            "middleware",
            "facturación",
        ),
        "id": 3001,
        "content_preview": "Integración con SAP: sincronización de facturación y maestro de artículos vía IDocs con reintentos.",
        "sector": "industrial",
        "budget_id": "BUD-SAP-2023-05",
        "estimated_hours": 860.0,
    },
    {
        "keywords": ("erp", "sap", "integración", "middleware", "clientes"),
        "id": 3002,
        "content_preview": "Middleware de integración ERP con capa de reintentos y monitorización de colas.",
        "sector": "industrial",
        "budget_id": "BUD-SAP-2021-12",
        "estimated_hours": 720.0,
    },
    {
        "keywords": ("móvil", "mobile", "app", "android", "ios", "offline", "repartidor", "firma"),
        "id": 4001,
        "content_preview": "App móvil de reparto (Android/iOS) con hoja de ruta, firma, foto y modo offline con sincronización.",
        "sector": "logistics",
        "budget_id": "BUD-APP-2023-03",
        "estimated_hours": 780.0,
    },
    {
        "keywords": ("móvil", "mobile", "app", "híbrida", "react native"),
        "id": 4002,
        "content_preview": "App híbrida React Native con sincronización offline y notificaciones push.",
        "sector": "ecommerce",
        "budget_id": "BUD-APP-2022-06",
        "estimated_hours": 640.0,
    },
    {
        "keywords": (
            "analítica",
            "analytics",
            "panel",
            "dashboard",
            "kpi",
            "cuadros",
            "alertas",
            "informes",
        ),
        "id": 5001,
        "content_preview": "Panel de analítica con cuadros de mando de KPIs, filtros por zona/cliente y alertas por umbral.",
        "sector": "logistics",
        "budget_id": "BUD-BI-2023-01",
        "estimated_hours": 560.0,
    },
    {
        "keywords": ("analítica", "analytics", "bi", "tendencias", "reporting"),
        "id": 5002,
        "content_preview": "Reporting BI con tendencias históricas y export programado.",
        "sector": "media",
        "budget_id": "BUD-BI-2022-04",
        "estimated_hours": 430.0,
    },
]


def search_budgets_stub(query: str, filters: dict | None = None) -> list[dict[str, Any]]:
    """Return canned historical items whose keywords appear in ``query``.

    ``filters`` may carry a ``sectors`` list; when present, results are restricted
    to those sectors. A crude ``distance`` is derived from how many keywords matched
    (more matches → smaller distance), so the shape mirrors a real vector search.
    """
    q = query.lower()
    sectors = None
    if filters and filters.get("sectors"):
        sectors = {s.lower() for s in filters["sectors"]}

    hits: list[dict[str, Any]] = []
    for entry in _CORPUS:
        matches = sum(1 for kw in entry["keywords"] if kw in q)
        if matches == 0:
            continue
        if sectors and entry["sector"].lower() not in sectors:
            continue
        hits.append(
            {
                "id": entry["id"],
                "content_preview": entry["content_preview"],
                "sector": entry["sector"],
                "budget_id": entry["budget_id"],
                "estimated_hours": entry["estimated_hours"],
                "distance": round(max(0.05, 0.6 - 0.1 * matches), 4),
            }
        )

    hits.sort(key=lambda h: h["distance"])
    return hits[:5]
