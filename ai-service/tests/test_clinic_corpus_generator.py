"""The clinic task-corpus generator is valid, ingestable, deterministic and TIGHT.

The last property is the one worth a test. Coverage of the per-task hours step is a
function of how many distinct template TEXTS the corpus holds, but the *quality* of a
match (``reliability = weighted_similarity × (1 − dispersion)``) is a function of how
much the neighbours' hours disagree — and with a synthetic corpus that disagreement is
manufactured by the generator's own ``randint(lo, hi)`` span, not by history. A wide
span therefore caps reliability below the green band no matter how close the match, so
the span ratio is a guarded invariant here, not a style preference.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.generation.rag.schemas import Budget

# The generators live in scripts/, which is not a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_clinic_corpus import (  # noqa: E402
    CLINIC_ABBREV,
    CLINIC_MODULES,
    ENGINEERING_MODULES,
    SECTOR,
    generate_corpus,
)

# Templates deliberately left wide so amber and low-reliability rows still occur —
# a corpus where every match is green is not a credible demo.
WIDE_ON_PURPOSE = {
    "Requirements discovery workshops",
    "User acceptance testing coordination",
}
MAX_SPAN_RATIO = 1.35


def test_generated_corpus_validates_as_budget():
    corpus = generate_corpus(count=5, seed=3)
    budgets = [Budget.model_validate(project) for project in corpus]
    assert len(budgets) == 5
    for budget in budgets:
        assert budget.components
        assert all(c.module for c in budget.components)
        assert budget.total_estimated_hours == sum(c.estimated_hours for c in budget.components)


def test_generation_is_deterministic_for_a_seed():
    assert generate_corpus(count=4, seed=11) == generate_corpus(count=4, seed=11)


def test_budget_ids_are_unique_and_namespaced():
    """``CLIN-`` keeps provenance readable and keeps source_paths clear of the base corpus."""
    corpus = generate_corpus(count=20, seed=41)
    ids = [p["budget_id"] for p in corpus]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("CLIN-") for i in ids)


def test_every_project_is_healthcare():
    """A new ``Sector`` literal value would 422 on ingest — this corpus reuses the existing one."""
    corpus = generate_corpus(count=10, seed=5)
    assert {p["client_metadata"]["sector"] for p in corpus} == {SECTOR}


def test_every_module_has_an_abbreviation():
    """``CLINIC_ABBREV[module]`` is a KeyError at generation time; assert it instead."""
    corpus = generate_corpus(count=15, seed=41)
    used = {c["module"] for p in corpus for c in p["components"]}
    assert used <= set(CLINIC_ABBREV)
    assert set(CLINIC_MODULES) <= set(CLINIC_ABBREV)
    assert set(ENGINEERING_MODULES) <= set(CLINIC_ABBREV)


def test_hour_ranges_are_tight_enough_to_reach_the_green_band():
    offenders = [
        (module, name, lo, hi)
        for catalog in (CLINIC_MODULES, ENGINEERING_MODULES)
        for module, templates in catalog.items()
        for name, _desc, _tech, (lo, hi), _complexity in templates
        if name not in WIDE_ON_PURPOSE and hi / lo > MAX_SPAN_RATIO
    ]
    assert not offenders, f"hour spans wider than {MAX_SPAN_RATIO}x: {offenders}"


def test_the_catalog_is_substantially_wider_than_the_base_one():
    """The whole point: distinct template texts, not more copies of the same ones."""
    total = sum(len(t) for t in CLINIC_MODULES.values())
    total += sum(len(t) for t in ENGINEERING_MODULES.values())
    assert total >= 90
