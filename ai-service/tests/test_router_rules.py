"""Unit tests for the cascade router.

The deterministic levels (explicit / rules / fallback) need no LLM; the
classifier level is exercised with a fake ``LLMWrapper`` so we never hit the API.
"""

from __future__ import annotations

import app.dependencies as deps
from app.generation.rag.retrieval.collections import ALL_COLLECTIONS, Collection
from app.generation.rag.retrieval.router import RouteClassification, route


class FakeWrapper:
    def __init__(self, classification: RouteClassification):
        self._classification = classification
        self.calls = 0

    def complete_structured(self, **kwargs):
        self.calls += 1
        return self._classification, {}


async def test_explicit_collections_short_circuit_the_cascade(monkeypatch):
    fake = FakeWrapper(RouteClassification(targets=[Collection.BUDGET], reason="x"))
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    decision = await route("anything", explicit=[Collection.TRANSCRIPT])

    assert decision.level == "explicit"
    assert decision.targets == [Collection.TRANSCRIPT]
    assert fake.calls == 0  # classifier never invoked


async def test_rules_win_before_the_classifier(monkeypatch):
    fake = FakeWrapper(RouteClassification(targets=[Collection.BUDGET], reason="x"))
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    decision = await route("what did the client say in the meeting")

    assert decision.level == "rules"
    assert decision.targets == [Collection.TRANSCRIPT]
    assert fake.calls == 0


async def test_classifier_runs_when_no_rule_fires(monkeypatch):
    fake = FakeWrapper(
        RouteClassification(targets=[Collection.BUDGET, Collection.TRANSCRIPT], reason="spans both")
    )
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    decision = await route("a query with no routing vocabulary at all")

    assert decision.level == "classifier"
    assert decision.targets == [Collection.BUDGET, Collection.TRANSCRIPT]
    assert decision.reason == "spans both"
    assert fake.calls == 1


async def test_fallback_searches_all_when_rules_and_classifier_disabled():
    decision = await route("xyzzy", rules_enabled=False, classifier_enabled=False)

    assert decision.level == "fallback"
    assert set(decision.targets) == set(ALL_COLLECTIONS)


async def test_classifier_failure_degrades_to_fallback(monkeypatch):
    class Boom:
        def complete_structured(self, **kwargs):
            raise RuntimeError("api down")

    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: Boom())

    decision = await route("a query with no routing vocabulary at all")

    assert decision.level == "fallback"


async def test_targets_are_capped_and_deduped(monkeypatch):
    fake = FakeWrapper(
        RouteClassification(targets=[Collection.BUDGET, Collection.BUDGET], reason="dup")
    )
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    decision = await route(
        "no vocabulary", max_targets=1, classifier_enabled=True, rules_enabled=False
    )

    assert decision.targets == [Collection.BUDGET]
