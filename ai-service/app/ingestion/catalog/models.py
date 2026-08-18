"""Pydantic v2 models for the data-source catalog.

The catalog is the contract between *what exists in the wild* and *what the
ingestion pipeline trusts*. Three decisions are valid:

* ``include`` — the source goes into the pipeline.
* ``review`` — known issues, awaiting an owner sign-off. Kept on disk, not
  ingested. Preserves the intermediate category instead of collapsing to a
  binary include/exclude (intentional disciplinary choice).
* ``exclude`` — explicitly out. A ``decision_reason`` is mandatory: excluding a
  source is a defensible call, not a silent omission.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CatalogDecision(str, Enum):
    INCLUDE = "include"
    REVIEW = "review"
    EXCLUDE = "exclude"


class QualityScore(BaseModel):
    """Per-dimension scoring on a 1..5 scale, anchored in concrete facts."""

    model_config = ConfigDict(extra="forbid")

    completeness: int = Field(ge=1, le=5)
    consistency: int = Field(ge=1, le=5)
    actuality: int = Field(ge=1, le=5)
    reliability: int = Field(ge=1, le=5)


class Sensitivity(BaseModel):
    """Whether the source contains PII and which kinds.

    Used downstream by the orchestrator to decide whether to route documents
    through the pseudonymization step before they are handed off for indexing.
    """

    model_config = ConfigDict(extra="forbid")

    has_pii: bool
    pii_flags: list[str] = Field(default_factory=list)
    access_level: Literal["public", "internal", "confidential"] = "internal"


class CatalogSource(BaseModel):
    """A single audited data source."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    location: str  # path or URL — resolved by the loader, not by this model
    owners: list[str] = Field(default_factory=list)
    format: Literal["json", "txt", "xlsx", "docx", "pdf", "csv"]
    volume_estimate: str = ""  # free-form, e.g. "≈ 6 ficheros, 250 KB"
    refresh_declared: str = ""  # what the owner says: "semanal"
    refresh_observed: str = ""  # what the inspector measured
    quality: QualityScore
    sensitivity: Sensitivity
    lineage: list[str] = Field(default_factory=list)
    decision: CatalogDecision
    decision_reason: str = ""
    last_audited: datetime | None = None

    @field_validator("name")
    @classmethod
    def _name_is_snake_case(cls, value: str) -> str:
        if not value or not all(c.islower() or c.isdigit() or c == "_" for c in value):
            raise ValueError(
                "CatalogSource.name must be lowercase snake_case "
                "(used as identifier in the ingestion endpoint)"
            )
        return value

    @model_validator(mode="after")
    def _decision_requires_reason_when_not_include(self) -> "CatalogSource":
        if self.decision is not CatalogDecision.INCLUDE and not self.decision_reason:
            raise ValueError(f"decision={self.decision.value} requires a non-empty decision_reason")
        return self


class DataCatalog(BaseModel):
    """The whole catalog. Versioned in git alongside the code."""

    model_config = ConfigDict(extra="forbid")

    version: str
    description: str = ""
    sources: list[CatalogSource]

    @field_validator("sources")
    @classmethod
    def _names_are_unique(cls, sources: list[CatalogSource]) -> list[CatalogSource]:
        seen: set[str] = set()
        for src in sources:
            if src.name in seen:
                raise ValueError(f"Duplicate source name in catalog: {src.name}")
            seen.add(src.name)
        return sources

    def included_sources(self) -> list[CatalogSource]:
        return [s for s in self.sources if s.decision is CatalogDecision.INCLUDE]

    def find(self, name: str) -> CatalogSource | None:
        for src in self.sources:
            if src.name == name:
                return src
        return None
