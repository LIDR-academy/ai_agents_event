"""Smoke test: ``evals/run.py`` end-to-end with the FakeLLMWrapper.

We mount the conversational dependencies onto the app via dependency_overrides
(same pattern as the integration tests), call the runner's helper, and assert
metrics flow through. Real LLM is never called.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.dependencies import (
    get_estimation_service,
    get_llm_wrapper,
    get_openai_client,
    get_session_store,
)
from app.main import app
from app.domain.schemas.estimation import EstimationResult
from app.domain.estimation_service import EstimationService
from app.generation.conversation.store import SessionStore
from evals.dataset import load_dataset
from evals.metrics import run_all_metrics
from tests.conftest import FakeLLMWrapper, make_canned_result


def test_dataset_loads_with_at_least_15_cases() -> None:
    cases = load_dataset()
    assert len(cases) >= 15
    # Out-of-scope mix is preserved.
    assert any(c.expected_out_of_scope for c in cases)


def test_run_against_fake_wrapper_collects_metrics_per_case(tmp_path: Path) -> None:
    wrapper = FakeLLMWrapper()
    # Make canned result big enough to land in most cost ranges in the dataset.
    wrapper.add_turn(result=make_canned_result(total_cost_eur=30_000, total_duration_weeks=8))

    store = SessionStore(max_turns=6)
    service = EstimationService(
        llm_wrapper=wrapper,
        exact_cache=None,
        semantic_cache=None,
        openai_client=None,
        metadata_extractor_model="gpt-4o-mini",
    )
    app.dependency_overrides[get_estimation_service] = lambda: service
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_llm_wrapper] = lambda: wrapper
    app.dependency_overrides[get_openai_client] = lambda: None

    try:
        cases = load_dataset()[:2]
        with TestClient(app) as client:
            for case in cases:
                sid = client.post("/sessions").json()["session_id"]
                form = {
                    "transcript": case.transcript,
                    "project_type": case.project_type,
                    "detail_level": case.detail_level,
                    "output_format": case.output_format,
                }
                response = client.post(f"/sessions/{sid}/estimate", data=form)
                assert response.status_code == 200, response.text
                payload = response.json()
                result = EstimationResult.model_validate(payload["result"])
                results = run_all_metrics(case, result)
                assert len(results) == 3
                assert all(r.name in {"schema_adherence", "cost_bounds", "content_recall"} for r in results)
    finally:
        app.dependency_overrides.clear()
