"""Unit tests for the Session 11 corpus-expansion service.

The underlying ``RagIngestService`` is stubbed; the focus is the batch loop:
counting indexed vs skipped documents, accumulating chunks, and reporting
progress.
"""

from __future__ import annotations

import asyncio

from app.generation.rag.index_service import CorpusIndexService
from app.generation.rag.ingest_service import DuplicateDocumentError
from app.generation.rag.schemas import Budget, IngestResponse


def _budget(budget_id: str, components: int = 2) -> Budget:
    return Budget.model_validate(
        {
            "budget_id": budget_id,
            "client_metadata": {"name": "Acme", "sector": "finance", "country": "ES"},
            "project_summary": "A new historical project",
            "main_technology": "Python",
            "year": 2025,
            "total_estimated_hours": 100 * components,
            "components": [
                {
                    "component_id": f"C-{i}",
                    "name": f"Component {i}",
                    "description": "work",
                    "module": "Core",
                    "tech_stack": ["Python"],
                    "estimated_hours": 100,
                    "complexity": "medium",
                    "dependencies": [],
                }
                for i in range(1, components + 1)
            ],
        }
    )


class _FakeIngest:
    """Stub RagIngestService: counts calls, raises Duplicate for known ids."""

    def __init__(self, duplicates: set[str]) -> None:
        self.duplicates = duplicates
        self.calls: list[str] = []

    async def ingest(self, *, source_path, document_type, budget, chunk_type):
        self.calls.append(source_path)
        if budget.budget_id in self.duplicates:
            raise DuplicateDocumentError(document_id=1)
        return IngestResponse(
            document_id=len(self.calls),
            chunks_created=len(budget.components),
            embedding_dimension=1536,
            ingestion_time_ms=1,
        )


def test_expand_counts_indexed_chunks_and_reports_progress():
    service = CorpusIndexService(ingest=_FakeIngest(duplicates=set()))
    docs = [_budget("NEW-1", 2), _budget("NEW-2", 3)]
    progress: list[int] = []

    result = asyncio.run(service.expand(docs, on_progress=progress.append))

    assert result.documents_indexed == 2
    assert result.documents_skipped == 0
    assert result.chunks_created == 5  # 2 + 3 components
    assert progress == [1, 2]  # processed count after each document


def test_expand_skips_duplicates_without_failing_the_batch():
    service = CorpusIndexService(ingest=_FakeIngest(duplicates={"DUP"}))
    docs = [_budget("NEW-1", 1), _budget("DUP", 4), _budget("NEW-2", 1)]

    result = asyncio.run(service.expand(docs))

    assert result.documents_indexed == 2
    assert result.documents_skipped == 1
    assert result.chunks_created == 2  # the duplicate's chunks are not counted


def test_expand_stamps_source_prefix_and_chunk_type():
    fake = _FakeIngest(duplicates=set())
    service = CorpusIndexService(ingest=fake)
    asyncio.run(service.expand([_budget("NEW-1", 1)], chunk_type="historical_task"))

    assert fake.calls == ["corpus-expansion::NEW-1"]
