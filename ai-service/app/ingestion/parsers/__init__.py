"""Parsers — bytes → ``Document``.

The :class:`Parser` is a :class:`typing.Protocol`, not a base class. Structural
typing is the right tool here: a parser is anything that exposes
``supported_formats`` and ``parse``. We never want a parser to inherit shared
state from a parent — they are pure functions over bytes.
"""

from app.ingestion.parsers.budget_json import BudgetJsonParser
from app.ingestion.parsers.protocol import Parser, ParseContext
from app.ingestion.parsers.registry import ParserRegistry, default_registry
from app.ingestion.parsers.transcript_txt import TranscriptTxtParser

__all__ = [
    "BudgetJsonParser",
    "Parser",
    "ParseContext",
    "ParserRegistry",
    "TranscriptTxtParser",
    "default_registry",
]
