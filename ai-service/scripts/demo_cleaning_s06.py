#!/usr/bin/env python3
"""Sub-block 2.4 demo — cleaning + Pandera validation over the seed budgets.

Runnable form of the live demo (no REPL typing): loads every budget JSON from
the seed corpus, runs ``clean_budget_records`` then ``validate_with_policy``,
and prints the partition report. Expected story:

* 6 ficheros entran, 5 llegan a validación (el dedup colapsa BUDGET-2024-0005).
* el ``total_amount: -50000`` se descarta; el resto pasa.

Usage::

    uv run python scripts/demo_cleaning_s06.py
    # or, inside docker:
    docker compose exec ai-service python scripts/demo_cleaning_s06.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Make ``app`` importable when this script runs directly from ``scripts/``.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingestion.cleaning import clean_budget_records, validate_with_policy  # noqa: E402

BUDGETS_DIR = ROOT / "data" / "seed" / "budgets"


def main() -> int:
    paths = sorted(BUDGETS_DIR.glob("*.json"))
    if not paths:
        print(f"ERROR: no budget JSON files under {BUDGETS_DIR}")
        return 1

    records = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    print(f"Ficheros leídos: {len(records)}  ({BUDGETS_DIR})")

    df = clean_budget_records(records)
    print(f"Filas tras limpieza + dedup: {len(df)}")

    result = validate_with_policy(df)
    print("\nReport:")
    for key, value in result.report.items():
        print(f"  {key}: {value}")

    if not result.discarded.empty:
        print("\nDescartadas:")
        print(result.discarded[["budget_id", "total_amount"]].to_string(index=False))
    if not result.quarantined.empty:
        print("\nEn cuarentena:")
        print(result.quarantined[["budget_id", "client_name"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
