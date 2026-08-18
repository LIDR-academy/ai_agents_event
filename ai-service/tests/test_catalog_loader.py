"""Tests for the YAML catalog loader and Pydantic validators."""
from __future__ import annotations

import textwrap

import pytest

from app.ingestion.catalog import CatalogDecision, load_catalog
from app.ingestion.catalog.models import (
    CatalogSource,
    DataCatalog,
    QualityScore,
    Sensitivity,
)


def _write_catalog(tmp_path, body: str):
    path = tmp_path / "catalog.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


_VALID_BODY = """
version: "1.0.0"
sources:
  - name: budgets_json
    location: budgets
    format: json
    quality: {completeness: 4, consistency: 3, actuality: 5, reliability: 4}
    sensitivity: {has_pii: true, pii_flags: ["PERSON"]}
    decision: include
  - name: legacy_txt
    location: transcripts
    format: txt
    quality: {completeness: 3, consistency: 2, actuality: 4, reliability: 3}
    sensitivity: {has_pii: true, pii_flags: ["PERSON"]}
    decision: review
    decision_reason: "legacy format pending owner sign-off"
"""


def test_load_catalog_happy_path(tmp_path):
    path = _write_catalog(tmp_path, _VALID_BODY)
    catalog = load_catalog(path)
    assert isinstance(catalog, DataCatalog)
    assert catalog.version == "1.0.0"
    assert len(catalog.sources) == 2
    included = catalog.included_sources()
    assert [s.name for s in included] == ["budgets_json"]


def test_load_catalog_real_repo_yaml():
    """The catalog shipped in data/ must always validate — it is the live demo."""
    catalog = load_catalog("data/catalog/catalog.yaml")
    assert catalog.included_sources(), "demo catalog should have at least one include"


def test_review_or_exclude_requires_reason(tmp_path):
    body = _VALID_BODY.replace(
        'decision_reason: "legacy format pending owner sign-off"', ""
    )
    path = _write_catalog(tmp_path, body)
    with pytest.raises(Exception):
        load_catalog(path)


def test_duplicate_source_names_rejected():
    payload = {
        "version": "1.0.0",
        "sources": [
            {
                "name": "dup",
                "location": "x",
                "format": "json",
                "quality": {
                    "completeness": 3,
                    "consistency": 3,
                    "actuality": 3,
                    "reliability": 3,
                },
                "sensitivity": {"has_pii": False, "pii_flags": []},
                "decision": "include",
            },
            {
                "name": "dup",
                "location": "y",
                "format": "json",
                "quality": {
                    "completeness": 3,
                    "consistency": 3,
                    "actuality": 3,
                    "reliability": 3,
                },
                "sensitivity": {"has_pii": False, "pii_flags": []},
                "decision": "include",
            },
        ],
    }
    with pytest.raises(Exception):
        DataCatalog.model_validate(payload)


def test_name_must_be_snake_case():
    with pytest.raises(Exception):
        CatalogSource(
            name="BudgetJSON",
            location="x",
            format="json",
            quality=QualityScore(
                completeness=3, consistency=3, actuality=3, reliability=3
            ),
            sensitivity=Sensitivity(has_pii=False, pii_flags=[]),
            decision=CatalogDecision.INCLUDE,
        )


def test_find_returns_source_or_none(tmp_path):
    path = _write_catalog(tmp_path, _VALID_BODY)
    catalog = load_catalog(path)
    assert catalog.find("budgets_json") is not None
    assert catalog.find("does_not_exist") is None
