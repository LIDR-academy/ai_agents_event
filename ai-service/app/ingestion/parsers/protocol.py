"""The :class:`Parser` protocol and the :class:`ParseContext` passed to it.

Why a Protocol instead of an abstract base class? Two reasons:

* Parsers share no implementation — there is no useful method to inherit.
* Structural typing makes it trivial to drop in a fake during tests without
  inheriting from anything; any class with ``supported_formats`` and ``parse``
  satisfies the contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol, runtime_checkable

from app.ingestion.catalog.models import CatalogSource
from app.ingestion.documents.models import Document
from app.ingestion.loaders.filesystem import LoadedBlob


@dataclass(frozen=True)
class ParseContext:
    """Everything a parser needs that does not live in the blob itself.

    The orchestrator builds this once per ingestion run and reuses it across
    blobs so that ``ingested_at`` and ``source_version`` are constant for the
    whole run — that constancy is what lets downstream dedupe by hash.
    """

    source: CatalogSource
    source_version: str
    ingested_at: datetime


@runtime_checkable
class Parser(Protocol):
    supported_formats: ClassVar[set[str]]

    def parse(
        self, blob: LoadedBlob, context: ParseContext
    ) -> Iterable[Document]:  # pragma: no cover - protocol stub
        ...
