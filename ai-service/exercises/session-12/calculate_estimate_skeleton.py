"""Skeleton for the deterministic ``calculate_estimate`` tool (Session 12).

This is a STARTING POINT so you don't waste time on the cost model and can focus on
the agent loop. It is a pure Python function — NO LLM call. Fill in the TODOs.

Contract (what the agent passes in and expects back):

    calculate_estimate({
        "components": [
            {"name": "Auth backend", "reference_amounts": [420.0, 380.0]},
            {"name": "Mobile app",   "reference_amounts": [780.0, 640.0]},
        ]
    })
    ->
    {
        "components": [
            {"name": "Auth backend", "reference_count": 2, "estimated_hours": ..., "unbudgeted": False},
            ...
        ],
        "total_hours": ...,
        "summary": "...",   # a short human-readable line for the trace observation
    }

The reference solution lives in ``app/generation/agentic/agent_tools.py`` — try it
yourself first, then compare.
"""

from __future__ import annotations

import statistics
from typing import Any

# A flat contingency buffer added to every component's central estimate. Keep it
# transparent — no hidden multipliers.
CONTINGENCY_FACTOR = 0.15


def calculate_estimate(args: dict[str, Any]) -> dict[str, Any]:
    """Cost each component from its historical reference amounts, then total."""
    components = args["components"]
    breakdown: list[dict[str, Any]] = []
    total = 0.0

    for component in components:
        name = component["name"]
        refs = component.get("reference_amounts", [])

        # TODO 1: choose a central estimate from `refs`. The median is robust to a
        #         single outlier budget; the mean is simpler. Pick one and justify it
        #         in the live session.
        # TODO 2: apply CONTINGENCY_FACTOR to the central estimate.
        # TODO 3: handle the empty-references case. Do NOT invent a number — cost it
        #         at 0 and flag it (`unbudgeted=True`) so the agent notices and can
        #         search again.
        if refs:
            central = statistics.median(refs)  # TODO: or mean — your call
            hours = round(central * (1 + CONTINGENCY_FACTOR), 1)
            unbudgeted = False
        else:
            hours = 0.0
            unbudgeted = True

        total += hours
        breakdown.append(
            {
                "name": name,
                "reference_count": len(refs),
                "estimated_hours": hours,
                "unbudgeted": unbudgeted,
            }
        )

    total = round(total, 1)
    # TODO 4: return a dict matching the contract above, including a short `summary`
    #         string used as the trace observation.
    return {
        "components": breakdown,
        "total_hours": total,
        "summary": f"total={total}h across {len(breakdown)} components",
    }
