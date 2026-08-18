"""HTTP-level tests for the persisting ``POST /embeddings/ingest`` (Session 8).

We bypass Postgres and OpenAI entirely: ``get_rag_ingest_service`` is overridden
with an in-memory fake that mimics the service contract (response model +
``DuplicateDocumentError``). This exercises the router wiring, the literal 409
shape and the error mapping without infrastructure.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_rag_ingest_service
from app.generation.rag.ingest_service import DuplicateDocumentError
from app.generation.rag.schemas import IngestResponse
from app.main import app


def make_budget_payload() -> dict:
    return {
        "budget_id": "BUD-2024-001",
        "client_metadata": {"name": "FintechCorp", "sector": "finance", "country": "ES"},
        "project_summary": "Mobile banking API with OAuth 2.0",
        "main_technology": "ruby_on_rails",
        "year": 2024,
        "total_estimated_hours": 120,
        "components": [
            {
                "component_id": "AUTH-001",
                "name": "OAuth 2.0 backend",
                "description": "OAuth flows with JWT session management.",
                "tech_stack": ["ruby_on_rails", "postgresql"],
                "estimated_hours": 120,
                "complexity": "high",
                "dependencies": [],
            }
        ],
    }


def make_ingest_payload() -> dict:
    return {
        "source_path": "data/budgets_sample.json::BUD-2024-001",
        "document_type": "historical_budget",
        "content": make_budget_payload(),
    }


class FakeRagIngestService:
    def __init__(self, *, duplicate_of: int | None = None) -> None:
        self.duplicate_of = duplicate_of
        self.calls: list[dict] = []

    async def ingest(
        self, *, source_path, document_type, budget, chunk_type="budget_component"
    ) -> IngestResponse:
        self.calls.append(
            {
                "source_path": source_path,
                "document_type": document_type,
                "budget": budget,
                "chunk_type": chunk_type,
            }
        )
        if self.duplicate_of is not None:
            raise DuplicateDocumentError(self.duplicate_of)
        return IngestResponse(
            document_id=7,
            chunks_created=1,
            embedding_dimension=1536,
            ingestion_time_ms=42,
        )


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.clear()


def test_ingest_persists_and_returns_contract():
    fake = FakeRagIngestService()
    app.dependency_overrides[get_rag_ingest_service] = lambda: fake

    response = TestClient(app).post("/embeddings/ingest", json=make_ingest_payload())

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "document_id": 7,
        "chunks_created": 1,
        "embedding_dimension": 1536,
        "ingestion_time_ms": 42,
    }
    # The router passes the validated Budget through, not the raw dict.
    assert fake.calls[0]["budget"].budget_id == "BUD-2024-001"
    # chunk_type defaults to budget_component when the payload omits it.
    assert fake.calls[0]["chunk_type"] == "budget_component"


def test_ingest_forwards_custom_chunk_type():
    fake = FakeRagIngestService()
    app.dependency_overrides[get_rag_ingest_service] = lambda: fake

    payload = make_ingest_payload() | {
        "document_type": "historical_task_breakdown",
        "chunk_type": "historical_task",
    }
    response = TestClient(app).post("/embeddings/ingest", json=payload)

    assert response.status_code == 200
    assert fake.calls[0]["chunk_type"] == "historical_task"


def test_ingest_duplicate_returns_409_with_literal_shape():
    app.dependency_overrides[get_rag_ingest_service] = lambda: FakeRagIngestService(duplicate_of=42)

    response = TestClient(app).post("/embeddings/ingest", json=make_ingest_payload())

    assert response.status_code == 409
    # Top-level shape from the exercise statement, NOT nested under "detail".
    assert response.json() == {"detail": "Document already ingested", "document_id": 42}


def test_ingest_service_unavailable_returns_500():
    app.dependency_overrides[get_rag_ingest_service] = lambda: None

    response = TestClient(app).post("/embeddings/ingest", json=make_ingest_payload())

    assert response.status_code == 500
    assert response.json()["detail"] == "Embedding service is not available."


def test_ingest_invalid_budget_returns_422_before_touching_the_service():
    fake = FakeRagIngestService()
    app.dependency_overrides[get_rag_ingest_service] = lambda: fake
    payload = make_ingest_payload()
    payload["content"].pop("components")  # Budget requires >= 1 component

    response = TestClient(app).post("/embeddings/ingest", json=payload)

    assert response.status_code == 422
    assert fake.calls == []


def test_ingest_embedding_failure_returns_500():
    class ExplodingService(FakeRagIngestService):
        async def ingest(self, **kwargs):
            raise RuntimeError("embeddings API down")

    app.dependency_overrides[get_rag_ingest_service] = lambda: ExplodingService()

    response = TestClient(app).post("/embeddings/ingest", json=make_ingest_payload())

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to generate embeddings."
