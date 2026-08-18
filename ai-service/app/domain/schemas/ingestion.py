"""Pydantic v2 schemas for the ingestion HTTP layer.

The API has two endpoints (POST + GET) and three response shapes. Keeping them
in one module makes the contract easy to read against the router code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IngestionRunRequest(BaseModel):
    """Body of ``POST /api/v1/ingestion/runs``."""

    model_config = ConfigDict(extra="forbid")

    source_name: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Name of an ``include`` source in the catalog. Anything else "
            "(unknown, review, exclude) is rejected with HTTP 400."
        ),
    )


class IngestionRunResponse(BaseModel):
    """Response of ``POST /api/v1/ingestion/runs``. Returned with HTTP 202."""

    job_id: uuid.UUID
    source_name: str
    status: Literal["pending", "running", "completed", "failed"]


class IngestionJobView(BaseModel):
    """Response of ``GET /api/v1/ingestion/jobs/{job_id}``."""

    job_id: uuid.UUID
    source_name: str
    status: Literal["pending", "running", "completed", "failed"]
    documents_count: int
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None
