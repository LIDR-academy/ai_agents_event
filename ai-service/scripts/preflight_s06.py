#!/usr/bin/env python3
"""Session 6 pre-flight verification.

Run this script BEFORE the live session to catch the usual setup pitfalls:
missing Spanish spaCy model, stale catalog, Postgres unreachable, etc. Output
is ✅ / ❌ per check; exit code is non-zero if anything fails.

Usage::

    uv run python scripts/preflight_s06.py
    # or, inside docker:
    docker compose exec ai-service python scripts/preflight_s06.py
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import sys
import urllib.request
from pathlib import Path
from typing import Callable

OK = "✅"
FAIL = "❌"

ROOT = Path(__file__).resolve().parent.parent
# Make ``app`` importable when this script runs directly from ``scripts/``.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Check:
    def __init__(self, name: str, fn: Callable[[], str | None]) -> None:
        self.name = name
        self._fn = fn

    def run(self) -> bool:
        try:
            detail = self._fn()
        except Exception as exc:  # noqa: BLE001
            print(f"{FAIL}  {self.name}: {type(exc).__name__}: {exc}")
            return False
        if detail:
            print(f"{OK}  {self.name}: {detail}")
        else:
            print(f"{OK}  {self.name}")
        return True


def check_python_version() -> str:
    major, minor, *_ = sys.version_info
    if (major, minor) < (3, 11):
        raise RuntimeError(f"Python 3.11+ required, found {major}.{minor}")
    return f"{major}.{minor}.{sys.version_info.micro}"


_MIN_VERSIONS = {
    "pandas": "2.2.0",
    "pandera": "0.20.0",
    "pyyaml": "6.0",
    "sqlalchemy": "2.0.0",
    "psycopg": "3.1.0",
    "alembic": "1.13.0",
    "openpyxl": "3.1.0",
    "presidio-analyzer": "2.2.0",
    "presidio-anonymizer": "2.2.0",
    "spacy": "3.7.0",
    "faker": "24.0.0",
    "unstructured": "0.14.0",
}


def _parse_version(s: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in s.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def check_packages() -> str:
    missing: list[str] = []
    too_old: list[str] = []
    for pkg, minimum in _MIN_VERSIONS.items():
        try:
            installed = importlib_metadata.version(pkg)
        except importlib_metadata.PackageNotFoundError:
            missing.append(pkg)
            continue
        if _parse_version(installed) < _parse_version(minimum):
            too_old.append(f"{pkg} {installed} < {minimum}")
    if missing or too_old:
        raise RuntimeError("missing=" + ",".join(missing) + " too_old=" + ",".join(too_old))
    return f"{len(_MIN_VERSIONS)} packages OK"


def check_spacy_model() -> str:
    import spacy

    nlp = spacy.load("es_core_news_md")
    doc = nlp("Laura Fernández firmó el contrato.")
    persons = [e.text for e in doc.ents if e.label_ == "PER"]
    if "Laura Fernández" not in " ".join(persons):
        raise RuntimeError(
            "es_core_news_md loaded but did NOT detect 'Laura Fernández' "
            "as PER — model may be too small or corrupted"
        )
    return f"loaded; detected PER entities: {persons}"


def check_unstructured_parsers() -> str:
    """unstructured.partition.auto must be importable for the reference repo."""
    import unstructured  # noqa: F401
    from unstructured.partition.auto import partition  # noqa: F401

    return f"unstructured {importlib_metadata.version('unstructured')}"


def check_corpus_seed() -> str:
    seed = ROOT / "data" / "seed"
    budgets = list((seed / "budgets").glob("*.json"))
    transcripts = list((seed / "transcripts").glob("*.txt"))
    xlsx = list(seed.glob("*.xlsx"))
    if not budgets or not transcripts or not xlsx:
        raise RuntimeError(
            f"corpus incomplete: budgets={len(budgets)}, "
            f"transcripts={len(transcripts)}, xlsx={len(xlsx)}"
        )
    return f"{len(budgets)} json + {len(transcripts)} txt + {len(xlsx)} xlsx"


def check_catalog() -> str:
    from app.ingestion.catalog import load_catalog

    catalog = load_catalog(ROOT / "data" / "catalog" / "catalog.yaml")
    return f"version {catalog.version}, {len(catalog.included_sources())} included"


def check_health_endpoint() -> str:
    try:
        with urllib.request.urlopen("http://localhost:8000/health", timeout=2) as r:
            return f"{r.status} OK"
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"GET /health failed — is the estimator running? ({exc})") from exc


def check_postgres() -> str:
    """Connect to Postgres and confirm the migration has been applied."""
    from sqlalchemy import inspect

    from app.foundation.persistence.database import create_engine_from_settings

    engine = create_engine_from_settings()
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    expected = {"pseudonym_mappings", "ingestion_jobs", "alembic_version"}
    missing = expected - tables
    if missing:
        raise RuntimeError(f"missing tables: {missing}; run `alembic upgrade head`")
    return f"tables OK: {sorted(expected)}"


CHECKS = [
    Check("Python version", check_python_version),
    Check("Required packages", check_packages),
    Check("spaCy es_core_news_md", check_spacy_model),
    Check("unstructured parsers", check_unstructured_parsers),
    Check("Corpus seed present", check_corpus_seed),
    Check("Catalog validates", check_catalog),
    Check("Estimator /health", check_health_endpoint),
    Check("Postgres + migration", check_postgres),
]


def main() -> int:
    print("Pre-flight Sesión 06\n")
    failed = 0
    for check in CHECKS:
        if not check.run():
            failed += 1
    print()
    if failed:
        print(f"{FAIL}  {failed} check(s) failed — fix before the live session")
        return 1
    print(f"{OK}  All checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
