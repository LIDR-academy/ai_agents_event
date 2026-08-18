"""Unit tests for query expansion/decomposition.

The technique heuristic and the degrade-to-direct behaviour are pure; the LLM
generation is faked so we test the parsing/capping of structured output without
hitting the API.
"""

from __future__ import annotations

import app.dependencies as deps
from app.generation.rag.retrieval.query_transform import (
    DECOMPOSE,
    DIRECT,
    EXPAND,
    QueryTransformation,
    SubQuery,
    choose_technique,
    transform_query,
)


class FakeWrapper:
    def __init__(self, transformation=None, raises: bool = False):
        self._transformation = transformation
        self._raises = raises
        self.calls = 0

    def complete_structured(self, **kwargs):
        self.calls += 1
        if self._raises:
            raise RuntimeError("api down")
        return self._transformation, {}


def test_choose_technique_by_shape():
    assert choose_technique("stripe checkout") == DIRECT  # too short
    assert choose_technique("secure mobile banking authentication backend service") == EXPAND
    assert (
        choose_technique(
            "we need oauth and a transaction ledger, plus a stripe checkout, and also "
            "personalized recommendations for the product catalog"
        )
        == DECOMPOSE
    )


async def test_disabled_returns_single_direct_query():
    plan = await transform_query("a moderately long single-topic query here", enabled=False)
    assert plan.technique == DIRECT
    assert len(plan.subqueries) == 1
    assert plan.subqueries[0].query == "a moderately long single-topic query here"


async def test_short_query_passes_through_without_llm(monkeypatch):
    fake = FakeWrapper()
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    plan = await transform_query("stripe checkout")

    assert plan.technique == DIRECT
    assert fake.calls == 0  # direct never calls the model


async def test_decomposition_parses_and_caps_subqueries(monkeypatch):
    # The schema already caps at 4 (the hard limit the model is held to); this
    # exercises the secondary runtime cap by asking for fewer.
    transformation = QueryTransformation(
        subqueries=[SubQuery(topic=f"t{i}", query=f"q{i}") for i in range(4)]
    )
    fake = FakeWrapper(transformation=transformation)
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    plan = await transform_query(
        "oauth and a ledger, plus stripe checkout, and also recommendations for the catalog",
        max_subqueries=2,
    )

    assert plan.technique == DECOMPOSE
    assert len(plan.subqueries) == 2  # capped by max_subqueries
    assert fake.calls == 1


async def test_generation_failure_degrades_to_direct(monkeypatch):
    fake = FakeWrapper(raises=True)
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    plan = await transform_query("secure mobile banking authentication backend service")

    assert plan.technique == DIRECT
    assert len(plan.subqueries) == 1
