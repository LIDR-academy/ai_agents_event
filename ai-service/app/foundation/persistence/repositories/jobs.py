"""Ingestion-job repository.

A job moves through ``pending → running → completed | failed``. The router
creates the row, the BackgroundTask transitions it, the GET endpoint reads it.
We never expose ORM types — callers see plain dataclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.foundation.persistence.models import IngestionJobRow

JobStatusName = Literal["pending", "running", "completed", "failed"]


@dataclass(frozen=True)
class Job:
    job_id: uuid.UUID
    source_name: str
    status: JobStatusName
    documents_count: int
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class JobsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, source_name: str) -> Job:
        row = IngestionJobRow(source_name=source_name, status="pending")
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _to_job(row)

    def get(self, job_id: uuid.UUID) -> Job | None:
        row = self._session.get(IngestionJobRow, job_id)
        return _to_job(row) if row is not None else None

    def mark_running(self, job_id: uuid.UUID) -> None:
        self._update(job_id, status="running")

    def set_documents_count(self, job_id: uuid.UUID, count: int) -> None:
        """Update the running progress counter (documents processed so far).

        Used by the Session 11 corpus-expansion job to report per-document
        progress while it runs; the GET endpoint reads it for the UI poller."""
        self._update(job_id, documents_count=count)

    def mark_completed(self, job_id: uuid.UUID, *, documents_count: int) -> None:
        self._update(
            job_id,
            status="completed",
            documents_count=documents_count,
            finished_at=datetime.now(timezone.utc),
        )

    def mark_failed(self, job_id: uuid.UUID, *, error_message: str) -> None:
        self._update(
            job_id,
            status="failed",
            error_message=error_message[:2048],
            finished_at=datetime.now(timezone.utc),
        )

    def _update(self, job_id: uuid.UUID, **fields) -> None:
        row = self._session.get(IngestionJobRow, job_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        self._session.commit()


def _to_job(row: IngestionJobRow) -> Job:
    return Job(
        job_id=row.job_id,
        source_name=row.source_name,
        status=row.status,  # type: ignore[arg-type]
        documents_count=row.documents_count,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def list_jobs(session: Session, *, limit: int = 50) -> list[Job]:
    """Debug helper used by tests and the preflight; not exposed via HTTP."""
    rows = (
        session.execute(
            select(IngestionJobRow).order_by(IngestionJobRow.started_at.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
    return [_to_job(r) for r in rows]
