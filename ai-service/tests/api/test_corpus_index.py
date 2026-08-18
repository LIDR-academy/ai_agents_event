"""HTTP-level tests for the Session 11 corpus-expansion endpoints.

Postgres is bypassed: ``get_session`` yields a stub, ``JobsRepository`` and
``SessionLocal`` are monkey-patched to an in-memory job store, the corpus service
runs against a fake ingest (no DB), and the stats endpoint's async factory +
store are stubbed. This exercises the router + BackgroundTask wiring in isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.routers.corpus_index as corpus_index
from app.dependencies import get_chunk_store, get_corpus_index_service
from app.foundation.persistence.database import get_session
from app.generation.rag.index_service import CorpusIndexService
from app.generation.rag.ingest_service import DuplicateDocumentError
from app.generation.rag.schemas import IngestResponse
from app.main import app


def _budget_dict(budget_id: str, components: int = 2) -> dict:
    return {
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


class _InMemoryJob:
    def __init__(self, source_name: str):
        self.job_id = uuid.uuid4()
        self.source_name = source_name
        self.status = "pending"
        self.documents_count = 0
        self.error_message = None
        self.started_at = datetime.now(timezone.utc)
        self.finished_at = None


class _InMemoryJobsRepo:
    _store: dict[uuid.UUID, _InMemoryJob] = {}

    def __init__(self, session=None) -> None:
        pass

    def create(self, *, source_name: str):
        job = _InMemoryJob(source_name)
        self._store[job.job_id] = job
        return job

    def get(self, job_id):
        return self._store.get(job_id)

    def mark_running(self, job_id):
        if job := self._store.get(job_id):
            job.status = "running"

    def set_documents_count(self, job_id, count):
        if job := self._store.get(job_id):
            job.documents_count = count

    def mark_completed(self, job_id, *, documents_count):
        if job := self._store.get(job_id):
            job.status = "completed"
            job.documents_count = documents_count
            job.finished_at = datetime.now(timezone.utc)

    def mark_failed(self, job_id, *, error_message):
        if job := self._store.get(job_id):
            job.status = "failed"
            job.error_message = error_message


class _FakeIngest:
    def __init__(self, duplicates: set[str]) -> None:
        self.duplicates = duplicates

    async def ingest(self, *, source_path, document_type, budget, chunk_type):
        if budget.budget_id in self.duplicates:
            raise DuplicateDocumentError(document_id=1)
        return IngestResponse(
            document_id=1,
            chunks_created=len(budget.components),
            embedding_dimension=1536,
            ingestion_time_ms=1,
        )


class _FakeStore:
    async def corpus_stats(self, session):
        return [
            ("budget", 10, 64, True),
            ("transcript", 2, 11, True),
            ("technical_doc", 1, 8, False),
        ]


class _FakeFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(corpus_index, "JobsRepository", _InMemoryJobsRepo)
    monkeypatch.setattr(corpus_index, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(corpus_index, "get_async_session_factory", lambda: _FakeFactory())
    app.dependency_overrides[get_session] = lambda: iter([None])
    app.dependency_overrides[get_corpus_index_service] = lambda: CorpusIndexService(
        ingest=_FakeIngest(duplicates={"DUP"})
    )
    app.dependency_overrides[get_chunk_store] = lambda: _FakeStore()
    _InMemoryJobsRepo._store.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_run_returns_202_with_job_and_total(client):
    body = {"documents": [_budget_dict("NEW-1"), _budget_dict("NEW-2")]}
    r = client.post("/embeddings/index/runs", json=body)
    assert r.status_code == 202, r.text
    payload = r.json()
    assert payload["documents_total"] == 2
    assert payload["status"] == "pending"
    assert "job_id" in payload


def test_job_reaches_completed_with_progress(client):
    # TestClient runs the async BackgroundTask after the response.
    r = client.post("/embeddings/index/runs", json={"documents": [_budget_dict("NEW-1", 3)]})
    job_id = r.json()["job_id"]
    got = client.get(f"/embeddings/index/jobs/{job_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "completed"
    assert body["documents_processed"] == 1


def test_duplicate_document_is_skipped_not_failed(client):
    body = {"documents": [_budget_dict("NEW-1"), _budget_dict("DUP")]}
    r = client.post("/embeddings/index/runs", json=body)
    job_id = r.json()["job_id"]
    body = client.get(f"/embeddings/index/jobs/{job_id}").json()
    assert body["status"] == "completed"
    # 1 indexed (NEW-1) + 1 skipped (DUP); mark_completed stores the indexed count.
    assert body["documents_processed"] == 1


def test_stats_returns_per_collection_growth_and_index_state(client):
    r = client.get("/embeddings/index/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_chunks"] == 64 + 11 + 8
    budget = next(c for c in body["collections"] if c["collection"] == "budget")
    assert budget["documents"] == 10
    assert budget["hnsw_indexed"] is True
    doc = next(c for c in body["collections"] if c["collection"] == "technical_doc")
    assert doc["hnsw_indexed"] is False


def test_run_requires_at_least_one_document(client):
    assert client.post("/embeddings/index/runs", json={"documents": []}).status_code == 422


def test_get_unknown_job_returns_404(client):
    fake = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/embeddings/index/jobs/{fake}").status_code == 404
