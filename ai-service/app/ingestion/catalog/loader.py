"""Load and validate the YAML catalog.

Reads a YAML file with ``yaml.safe_load``, validates it against
:class:`DataCatalog`. The two-line ``load_catalog`` is intentionally trivial —
all the heavy lifting (decision rules, duplicate detection) lives in the
model, not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from app.ingestion.catalog.models import DataCatalog


def load_catalog(path: str | Path) -> DataCatalog:
    """Parse ``path`` as YAML and validate as a :class:`DataCatalog`."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DataCatalog.model_validate(raw)


def _main(argv: list[str]) -> int:
    """CLI: ``python -m app.ingestion.catalog.loader data/catalog/catalog.yaml``."""
    if len(argv) != 2:
        print("usage: python -m app.ingestion.catalog.loader <path.yaml>")
        return 1
    catalog = load_catalog(argv[1])
    included = catalog.included_sources()
    print(f"Catalog version: {catalog.version}")
    print(f"Sources total:  {len(catalog.sources)}")
    print(f"Sources included: {len(included)}")
    for src in included:
        print(f"  - {src.name} ({src.format}) — {src.description!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
