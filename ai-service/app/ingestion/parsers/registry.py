"""Tiny parser registry — maps a format string to a :class:`Parser` instance.

The registry is a simple dict in disguise. We keep it as a class to make the
"only one parser per format" invariant enforceable and to give the orchestrator
a single object to depend on.
"""

from __future__ import annotations

from app.ingestion.parsers.budget_json import BudgetJsonParser
from app.ingestion.parsers.protocol import Parser
from app.ingestion.parsers.transcript_txt import TranscriptTxtParser


class ParserRegistry:
    def __init__(self) -> None:
        self._by_format: dict[str, Parser] = {}

    def register(self, parser: Parser) -> None:
        for fmt in parser.supported_formats:
            if fmt in self._by_format:
                raise ValueError(
                    f"Format {fmt!r} already has a registered parser "
                    f"({type(self._by_format[fmt]).__name__})"
                )
            self._by_format[fmt] = parser

    def get(self, fmt: str) -> Parser:
        try:
            return self._by_format[fmt]
        except KeyError as exc:
            raise KeyError(f"No parser registered for format {fmt!r}") from exc

    def formats(self) -> set[str]:
        return set(self._by_format)


def default_registry() -> ParserRegistry:
    """Return a registry preloaded with the parsers available in this branch.

    XLSX/DOCX/PDF parsers are intentionally NOT registered here in Session 6;
    they ship in ``guides/session-06-reference/``. Adding them later is a
    one-line ``registry.register(XlsxParser())`` call.
    """
    registry = ParserRegistry()
    registry.register(BudgetJsonParser())
    registry.register(TranscriptTxtParser())
    return registry
