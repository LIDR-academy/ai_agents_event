"""The canonical ``Document`` model.

Designed flat on purpose: a single ``DocumentMetadata`` sub-object holds the
structured fields that every consumer needs; everything format-specific lives
in ``extra: dict[str, Any]``. That escape hatch is what lets us keep the
contract uniform across JSON, TXT, XLSX and future formats without inventing
sub-types per source.

The ``id`` is the deterministic identifier the orchestrator assigns; it must be
stable across re-ingestions so the downstream index can dedupe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    source_version: str  # the ``DataCatalog.version`` active at ingestion time
    ingested_at: datetime
    lineage: list[str] = Field(default_factory=list)
    sensitivity_pii_flags: list[str] = Field(default_factory=list)
    sensitivity_access_level: str = "internal"
    location: str = ""  # path or URL the document came from
    extra: dict[str, Any] = Field(default_factory=dict)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    metadata: DocumentMetadata
