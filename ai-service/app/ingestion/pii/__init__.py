"""GDPR-compliant pseudonymization layer.

Three concerns, three modules:

* ``analyzer`` — initializes Presidio's ``AnalyzerEngine`` against
  ``es_core_news_md``. Without an explicit Spanish model, Presidio defaults to
  English and silently misses Hispanic names — a teaching moment in the live
  session.

* ``recognizers`` — custom :class:`PatternRecognizer`s for the project's
  domain-specific identifiers (``BUDGET-YYYY-NNNN`` and ``CLI-NNNN``).

* ``pseudonymizer`` — the :class:`ConsistentPseudonymizer`. Same original
  value → same pseudonym, across documents, across runs. Backed by a
  persistent :class:`MappingStore` so the operation is reversible (Art. 17).
"""

from app.ingestion.pii.analyzer import build_analyzer
from app.ingestion.pii.mapping_store import (
    InMemoryMappingStore,
    MappingStore,
    PostgresMappingStore,
)
from app.ingestion.pii.pseudonymizer import (
    ConsistentPseudonymizer,
    PseudonymizationResult,
)
from app.ingestion.pii.recognizers import (
    BudgetIdRecognizer,
    ClientCodeRecognizer,
)

__all__ = [
    "BudgetIdRecognizer",
    "ClientCodeRecognizer",
    "ConsistentPseudonymizer",
    "InMemoryMappingStore",
    "MappingStore",
    "PostgresMappingStore",
    "PseudonymizationResult",
    "build_analyzer",
]
