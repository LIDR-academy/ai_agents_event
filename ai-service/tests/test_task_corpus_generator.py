"""The task-corpus generator output is valid, ingestable and deterministic (S09)."""

from __future__ import annotations

import sys
from pathlib import Path

from app.generation.rag.schemas import Budget

# The generator lives in scripts/, which is not a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_task_corpus import generate_corpus  # noqa: E402


def test_generated_corpus_validates_as_budget():
    corpus = generate_corpus(count=6, seed=1)
    budgets = [Budget.model_validate(project) for project in corpus]
    assert len(budgets) == 6
    # Every task carries its module and the recorded total matches the sum.
    for budget in budgets:
        assert budget.components, "each project has at least one task"
        assert all(c.module for c in budget.components)
        assert budget.total_estimated_hours == sum(c.estimated_hours for c in budget.components)


def test_generation_is_deterministic_for_a_seed():
    assert generate_corpus(count=4, seed=7) == generate_corpus(count=4, seed=7)


def test_budget_ids_are_unique():
    corpus = generate_corpus(count=12, seed=90)
    ids = [project["budget_id"] for project in corpus]
    assert len(ids) == len(set(ids))


def test_corpus_is_task_granular():
    # The whole point: many more tasks than the coarse 60-component corpus.
    corpus = generate_corpus(count=12, seed=90)
    tasks = sum(len(project["components"]) for project in corpus)
    assert tasks >= 120
    modules = {c["module"] for project in corpus for c in project["components"]}
    assert len(modules) >= 8


def test_default_corpus_is_large_and_diverse():
    # The default corpus was scaled up (60 projects) and broadened: it must span
    # all eight sectors and a wide set of modules so the per-task hours search has
    # plenty of historical analogs to match.
    corpus = generate_corpus()  # default count/seed
    assert len(corpus) == 60
    budgets = [Budget.model_validate(project) for project in corpus]
    tasks = sum(len(b.components) for b in budgets)
    assert tasks >= 800
    sectors = {b.client_metadata.sector for b in budgets}
    assert sectors == {
        "finance",
        "ecommerce",
        "healthcare",
        "industrial",
        "logistics",
        "education",
        "media",
        "government",
    }
    modules = {c.module for b in budgets for c in b.components}
    assert len(modules) >= 20
    ids = [b.budget_id for b in budgets]
    assert len(ids) == len(set(ids))
