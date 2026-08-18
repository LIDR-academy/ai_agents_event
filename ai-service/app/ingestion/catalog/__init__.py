"""Versioned data-source catalog.

A ``DataCatalog`` is *code* (Pydantic + YAML in git), not documentation. Each
``CatalogSource`` declares what we know about a source — owners, refresh
cadence, quality scores, sensitivity, lineage — and an explicit
``decision`` (include / review / exclude) with a reason for non-includes.
"""

from app.ingestion.catalog.loader import load_catalog
from app.ingestion.catalog.models import (
    CatalogDecision,
    CatalogSource,
    DataCatalog,
    QualityScore,
    Sensitivity,
)

__all__ = [
    "CatalogDecision",
    "CatalogSource",
    "DataCatalog",
    "QualityScore",
    "Sensitivity",
    "load_catalog",
]
