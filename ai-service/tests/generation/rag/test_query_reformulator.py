"""Unit tests for query reformulation (Session 9)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.dependencies as deps
from app.generation.rag import query_reformulator as qr
from app.generation.rag.errors import ReformulationError
from app.generation.rag.schemas import EstimationQuery


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    monkeypatch.setattr(
        qr,
        "get_settings",
        lambda: SimpleNamespace(REFORMULATION_MODEL="gpt-5-mini", GENERATION_MAX_TOKENS=32_000),
    )


class FakeWrapper:
    """Records calls; scripts ``complete_structured`` and ``complete``."""

    def __init__(self, *, structured=None, structured_error=None, completion=None):
        self._structured = structured
        self._structured_error = structured_error
        self._completion = completion
        self.structured_calls = 0
        self.completion_calls = 0

    def complete_structured(self, **kwargs):
        self.structured_calls += 1
        if self._structured_error is not None:
            raise self._structured_error
        return self._structured, {"model": "gpt-5-mini"}

    def complete(self, **kwargs):
        self.completion_calls += 1
        return self._completion


async def test_reformulate_query_happy_path(monkeypatch):
    expected = EstimationQuery(
        function="B2B payments marketplace",
        technologies=["Stripe Connect"],
        sector="finance",
        scale="medium",
    )
    fake = FakeWrapper(structured=expected)
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    result = await qr.reformulate_query("long messy transcript ...")

    assert result == expected
    assert fake.structured_calls == 1
    assert fake.completion_calls == 0


async def test_reformulate_query_falls_back_to_free_text(monkeypatch):
    fake = FakeWrapper(
        structured_error=RuntimeError("schema validation failed"),
        completion={"estimation": "ecommerce storefront with card checkout"},
    )
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    result = await qr.reformulate_query("transcript the model could not structure")

    assert isinstance(result, EstimationQuery)
    assert result.function == "ecommerce storefront with card checkout"
    # Only ``function`` is populated on the degraded path.
    assert result.technologies == []
    assert fake.structured_calls == 1
    assert fake.completion_calls == 1


async def test_reformulate_query_raises_when_both_paths_fail(monkeypatch):
    fake = FakeWrapper(
        structured_error=RuntimeError("boom"),
        completion={"estimation": "   "},  # empty rewrite
    )
    monkeypatch.setattr(deps, "get_llm_wrapper", lambda: fake)

    with pytest.raises(ReformulationError):
        await qr.reformulate_query("transcript")


def test_compose_search_text_joins_grounded_fields():
    query = EstimationQuery(
        function="B2B payments marketplace platform",
        technologies=["Stripe Connect", "KYC"],
        sector="healthcare",
        country="Germany",
        regulations=["BaFin"],
    )
    text = qr.compose_search_text(query)

    assert text.startswith("B2B payments marketplace platform")
    assert "with Stripe Connect, KYC" in text
    assert "for healthcare" in text
    assert "in Germany" in text
    assert "BaFin-compliant" in text


def test_compose_search_text_drops_empty_fields():
    query = EstimationQuery(function="simple internal tool")
    assert qr.compose_search_text(query) == "simple internal tool"
