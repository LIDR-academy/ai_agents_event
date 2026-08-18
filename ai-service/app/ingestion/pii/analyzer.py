"""Presidio AnalyzerEngine factory configured for Spanish text.

The default Presidio configuration ships with English spaCy models. On Hispanic
names ("Laura Fernández", "Javier Romero") it silently returns *zero* PERSON
entities — a failure mode that is much worse than a false positive because it
goes undetected. This module is the single place that wires Presidio to
``es_core_news_md``.

The live session uses this module by first SHOWING the failure with the
default configuration and THEN replacing it with this one.
"""

from __future__ import annotations

from functools import lru_cache

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.config import get_settings
from app.ingestion.pii.recognizers import (
    BudgetIdRecognizer,
    ClientCodeRecognizer,
)


@lru_cache
def build_analyzer() -> AnalyzerEngine:
    """Return a Presidio AnalyzerEngine wired to the configured Spanish model.

    Singleton on purpose: spaCy model load is slow (~300 ms) and Presidio is
    thread-safe for read-only analysis. Custom recognizers (BUDGET_ID,
    CLIENT_CODE) are registered eagerly so callers don't have to remember to.
    """
    settings = get_settings()
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "es", "model_name": settings.PRESIDIO_SPACY_MODEL}],
        }
    )
    engine = AnalyzerEngine(
        nlp_engine=provider.create_engine(),
        supported_languages=["es"],
    )
    engine.registry.add_recognizer(BudgetIdRecognizer())
    engine.registry.add_recognizer(ClientCodeRecognizer())
    return engine
