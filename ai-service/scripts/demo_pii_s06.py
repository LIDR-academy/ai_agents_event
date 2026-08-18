#!/usr/bin/env python3
"""Sub-block 2.5 demo — pseudonymization with Presidio (Spanish) + mapping table.

Runnable form of the live demo (no REPL typing). Builds ``build_analyzer()``
wired to ``es_core_news_md`` + the custom recognizers, then runs a
``ConsistentPseudonymizer`` over a real transcript and prints the result.

Why this script does NOT run the "English fails" contrast: forcing the default
English engine triggers a ~382 MB ``en_core_web_lg`` download and, inside
docker (only the Spanish model is installed), would crash. That failure is a
spoken teaching point — its rationale is documented in
``app/ingestion/pii/analyzer.py``. Here we show the *fix* working.

Usage::

    uv run python scripts/demo_pii_s06.py
    # or, inside docker:
    docker compose exec ai-service python scripts/demo_pii_s06.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A salt is required to build the pseudonymizer; use a demo one if unset.
os.environ.setdefault("PSEUDONYM_HASH_SALT", "demo-salt")

from app.ingestion.pii import (  # noqa: E402
    ConsistentPseudonymizer,
    InMemoryMappingStore,
    build_analyzer,
)

TRANSCRIPT = ROOT / "data" / "seed" / "transcripts" / "transcripcion_2025-02-03_betanorte.txt"


def main() -> int:
    if not TRANSCRIPT.exists():
        print(f"ERROR: no existe {TRANSCRIPT}")
        return 1

    pseudo = ConsistentPseudonymizer(
        analyzer=build_analyzer(),  # es_core_news_md + BUDGET_ID/CLIENT_CODE recognizers
        mapping_store=InMemoryMappingStore(),
        salt=os.environ["PSEUDONYM_HASH_SALT"],
    )
    result = pseudo.pseudonymize(TRANSCRIPT.read_text(encoding="utf-8"))

    print(f"Transcripción: {TRANSCRIPT.name}")
    print("\n--- Texto pseudonimizado (primeros 400 chars) ---")
    print(result.pseudonymized_text[:400])

    print(f"\n--- Mappings aplicados ({len(result.applied)}) ---")
    for m in result.applied[:12]:
        print(f"  {m.entity_type:14} → {m.pseudonym}")
    print(
        "\nMismo valor original → mismo pseudónimo en todo el documento "
        "(consistencia por valor, no por chunk)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
