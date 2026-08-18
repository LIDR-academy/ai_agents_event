"""Tests for ConsistentPseudonymizer.

We do NOT touch the real Presidio analyzer in unit tests — loading spaCy
``es_core_news_md`` is slow and brittle in CI. We use a stub analyzer that
emits canned :class:`RecognizerResult` objects so we can exercise the
substitution logic, the consistency contract and the mapping store integration.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.pii import ConsistentPseudonymizer, InMemoryMappingStore


@dataclass
class _StubResult:
    """Minimal stand-in for presidio_analyzer.RecognizerResult."""

    entity_type: str
    start: int
    end: int
    score: float = 0.95


class StubAnalyzer:
    """Returns scripted results in document order."""

    def __init__(self, results_by_text: dict[str, list[_StubResult]]) -> None:
        self._results = results_by_text

    def analyze(self, *, text: str, language: str, entities=None):
        return list(self._results.get(text, []))


def _pseudonymizer(analyzer, store=None):
    return ConsistentPseudonymizer(
        analyzer=analyzer,
        mapping_store=store or InMemoryMappingStore(),
        salt="unit-test-salt",
        faker_locale="es_ES",
        language="es",
    )


def test_consistency_same_value_same_pseudonym_across_calls():
    text1 = "Habló con Laura Fernández ayer."
    text2 = "Laura Fernández firmó esta mañana."
    analyzer = StubAnalyzer(
        {
            text1: [_StubResult("PERSON", text1.index("Laura Fernández"),
                                text1.index("Laura Fernández") + len("Laura Fernández"))],
            text2: [_StubResult("PERSON", text2.index("Laura Fernández"),
                                text2.index("Laura Fernández") + len("Laura Fernández"))],
        }
    )
    store = InMemoryMappingStore()
    pseudo = _pseudonymizer(analyzer, store)
    r1 = pseudo.pseudonymize(text1)
    r2 = pseudo.pseudonymize(text2)
    # Both invocations use the SAME pseudonym for "Laura Fernández".
    assert r1.applied[0].pseudonym == r2.applied[0].pseudonym
    # And the pseudonym actually replaced the original in the output.
    assert "Laura Fernández" not in r1.pseudonymized_text
    assert "Laura Fernández" not in r2.pseudonymized_text


def test_different_values_get_different_pseudonyms():
    text = "Laura Fernández y Javier Romero firmaron."
    span_laura = (text.index("Laura Fernández"), text.index("Laura Fernández") + len("Laura Fernández"))
    span_javier = (text.index("Javier Romero"), text.index("Javier Romero") + len("Javier Romero"))
    analyzer = StubAnalyzer(
        {
            text: [
                _StubResult("PERSON", *span_laura),
                _StubResult("PERSON", *span_javier),
            ]
        }
    )
    r = _pseudonymizer(analyzer).pseudonymize(text)
    pseudonyms = {m.pseudonym for m in r.applied}
    assert len(pseudonyms) == 2


def test_budget_id_recognizer_friendly_replacement():
    text = "Revisando BUDGET-2024-0001 con el cliente."
    span = (
        text.index("BUDGET-2024-0001"),
        text.index("BUDGET-2024-0001") + len("BUDGET-2024-0001"),
    )
    analyzer = StubAnalyzer({text: [_StubResult("BUDGET_ID", *span)]})
    r = _pseudonymizer(analyzer).pseudonymize(text)
    assert r.applied[0].entity_type == "BUDGET_ID"
    assert r.applied[0].pseudonym.startswith("BUDGET-")
    assert "BUDGET-2024-0001" not in r.pseudonymized_text


def test_overlapping_entities_replaced_right_to_left():
    """Sorting by end-offset descending keeps earlier offsets valid."""
    text = "Carmen escribió a carmen.vidal@toledo-ind.es y firmó."
    span_person = (text.index("Carmen"), text.index("Carmen") + len("Carmen"))
    span_email = (
        text.index("carmen.vidal@toledo-ind.es"),
        text.index("carmen.vidal@toledo-ind.es") + len("carmen.vidal@toledo-ind.es"),
    )
    analyzer = StubAnalyzer(
        {
            text: [
                _StubResult("PERSON", *span_person),
                _StubResult("EMAIL_ADDRESS", *span_email),
            ]
        }
    )
    r = _pseudonymizer(analyzer).pseudonymize(text)
    assert "Carmen" not in r.pseudonymized_text.split()  # whole word replaced
    assert "carmen.vidal@toledo-ind.es" not in r.pseudonymized_text


def test_no_pii_means_no_changes():
    text = "Una frase sin nada interesante."
    analyzer = StubAnalyzer({text: []})
    r = _pseudonymizer(analyzer).pseudonymize(text)
    assert r.pseudonymized_text == text
    assert r.applied == []
