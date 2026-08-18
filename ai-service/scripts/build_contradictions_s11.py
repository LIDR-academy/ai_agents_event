#!/usr/bin/env python3
"""Ingest the Session 11 contradictory-sources fixture (40h vs 90h).

The per-task hours consensus (Session 10) averages the nearest historical tasks
into one number. Session 11 shows what that hides: when analogs genuinely
disagree, the average is a lie. This fixture plants two historical projects that
share a task name — "Real-time notifications delivery service" — but scoped very
differently: a lean email-only build (40h) and a regulated multi-channel platform
(90h). A wizard task that matches both makes the neighbour dispersion spike, and
synthesis surfaces a 40–90h RANGE with a reason instead of a misleading ~65h
point.

Ingested with the SAME tags as the base task corpus
(``document_type='historical_task_breakdown'`` / ``chunk_type='historical_task'``),
so it is retrieved by the same filtered search and coexists with it. Idempotent
(409 = already present). Reuses the base corpus ingest helpers — no new HTTP code.

Usage::

    docker compose exec ai-service python scripts/build_contradictions_s11.py
    uv run python scripts/build_contradictions_s11.py            # host, API on :8000

Wipe it (or the whole task corpus) anytime::

    DELETE FROM documents WHERE document_type = 'historical_task_breakdown';
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_task_corpus import ingest_corpus  # noqa: E402

FIXTURE_PATH = ROOT / "data" / "task_corpus_contradictions.json"


def main() -> None:
    corpus = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(corpus)} contradictory projects from {FIXTURE_PATH.name}.")
    ingest_corpus(corpus)


if __name__ == "__main__":
    main()
