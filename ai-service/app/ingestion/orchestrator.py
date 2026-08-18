"""Orchestrator: glues catalog + loader + parser into a single ingestion run.

The contract is intentionally narrow:

    ingest_source(catalog, source_name, *, loader, registry, jobs_repo, job_id)
      → list[Document]

Three guarantees:

1. The source must exist in the catalog and have ``decision == include``.
   Anything else raises ``IngestionRejected`` — the router maps that to 400.
2. ``jobs_repo`` is updated as the run progresses: pending → running → completed
   or failed. The router can poll it via the GET endpoint.
3. The function is *synchronous*. It is invoked from a FastAPI BackgroundTask;
   the HTTP layer wraps it in a session-scoped DB session.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog

from app.ingestion.catalog.models import (
    CatalogDecision,
    DataCatalog,
)
from app.ingestion.documents.models import Document
from app.ingestion.loaders.filesystem import FileSystemLoader
from app.ingestion.parsers.protocol import ParseContext
from app.ingestion.parsers.registry import ParserRegistry
from app.foundation.persistence.repositories.jobs import JobsRepository

log = structlog.get_logger(__name__)


class IngestionRejected(Exception):
    """The source is unknown or excluded/review — the run cannot start."""


def ingest_source(
    *,
    catalog: DataCatalog,
    source_name: str,
    loader: FileSystemLoader,
    registry: ParserRegistry,
    jobs_repo: JobsRepository,
    job_id: uuid.UUID,
) -> list[Document]:
    """Run the ingestion for a single included source.

    Side effects:
      * Updates the ``ingestion_jobs`` row referenced by ``job_id``.
      * Logs structured events at the source boundary.
    """
    bound = log.bind(job_id=str(job_id), source_name=source_name)

    source = catalog.find(source_name)
    if source is None:
        raise IngestionRejected(f"source {source_name!r} not found in catalog")
    if source.decision is not CatalogDecision.INCLUDE:
        raise IngestionRejected(
            f"source {source_name!r} has decision={source.decision.value!r}; "
            f"only 'include' sources can be ingested"
        )

    jobs_repo.mark_running(job_id)
    bound.info("ingestion.started", format=source.format)

    parser = registry.get(source.format)
    context = ParseContext(
        source=source,
        source_version=catalog.version,
        ingested_at=datetime.now(timezone.utc),
    )

    documents: list[Document] = []
    try:
        for blob in loader.iter_blobs(source.location, {source.format}):
            for document in parser.parse(blob, context):
                documents.append(document)
    except Exception as exc:
        bound.error("ingestion.failed", error=str(exc))
        jobs_repo.mark_failed(job_id, error_message=str(exc))
        raise

    jobs_repo.mark_completed(job_id, documents_count=len(documents))
    bound.info("ingestion.completed", documents_count=len(documents))
    return documents
