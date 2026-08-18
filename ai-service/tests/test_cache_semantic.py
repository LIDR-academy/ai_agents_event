"""Tests for the semantic cache. Mocked: redisvl needs Redis Stack to run for
real, so we test the behaviour of ``EstimationSemanticCache`` against a fake
``SearchIndex`` and a fake vectorizer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from app.domain.schemas.estimation import EstimationRequest, EstimationResult


def _valid_request() -> EstimationRequest:
    return EstimationRequest(
        description="Mobile app for booking medical appointments with login dashboard and calendar.",
        project_type="mobile_app",
        detail_level="summary",
        output_format="narrative",
    )


def _canned_result() -> EstimationResult:
    return EstimationResult(
        summary="Standard mobile app for appointment management.",
        confidence_pct=70,
        phases=[
            {"name": "Discovery", "duration_weeks": 1, "cost_eur": 5_000,
             "summary": "Scoping and tech spike."},
            {"name": "Build", "duration_weeks": 6, "cost_eur": 20_000,
             "summary": "Core feature implementation."},
        ],
        total_duration_weeks=7,
        total_cost_eur=25_000,
    )


def _build_cache(*, threshold: float = 0.92, log_only: bool = False, hits=None):
    """Build a semantic cache with a fully fake SearchIndex + vectorizer."""
    fake_index = MagicMock()
    fake_index.create.return_value = None
    fake_index.set_client.return_value = None
    fake_index.query.return_value = hits or []
    fake_index.load.return_value = None
    fake_vectorizer = SimpleNamespace(embed=lambda text: [0.1] * 1536)

    with patch("app.generation.cag.semantic.SearchIndex") if False else _NoopPatcher():
        from app.generation.cag.semantic import EstimationSemanticCache

    # Re-import to ensure the module is loaded, then build the cache and
    # replace the internals with our fakes.
    cache = EstimationSemanticCache.__new__(EstimationSemanticCache)
    cache.redis_client = MagicMock()
    cache.vectorizer = fake_vectorizer
    cache.threshold = threshold
    cache.ttl = 60
    cache.log_only = log_only
    cache.index = fake_index
    return cache, fake_index, fake_vectorizer


class _NoopPatcher:
    def __enter__(self):
        return None

    def __exit__(self, *_):
        return False


# --- Bucket key -------------------------------------------------------------


def test_bucket_includes_all_form_options() -> None:
    from app.generation.cag.semantic import EstimationSemanticCache

    request = _valid_request()
    bucket = EstimationSemanticCache.bucket_for(request, prompt_version="v1", model="gpt-4o-mini")
    assert bucket == "v1:mobile_app:summary:narrative:gpt-4o-mini"


def test_bucket_changes_when_any_option_changes() -> None:
    from app.generation.cag.semantic import EstimationSemanticCache

    base = _valid_request()
    other = EstimationRequest.model_validate(
        {**base.model_dump(), "output_format": "phases_table"}
    )
    assert EstimationSemanticCache.bucket_for(
        base, prompt_version="v1", model="gpt-4o-mini"
    ) != EstimationSemanticCache.bucket_for(other, prompt_version="v1", model="gpt-4o-mini")


def test_bucket_changes_when_model_changes() -> None:
    # The runtime model override must partition the semantic cache: a hit
    # produced by one model is never served while another model is active.
    from app.generation.cag.semantic import EstimationSemanticCache

    request = _valid_request()
    assert EstimationSemanticCache.bucket_for(
        request, prompt_version="v1", model="gpt-4o-mini"
    ) != EstimationSemanticCache.bucket_for(request, prompt_version="v1", model="gpt-4o")


def test_bucket_changes_when_prompt_version_changes() -> None:
    from app.generation.cag.semantic import EstimationSemanticCache

    request = _valid_request()
    assert EstimationSemanticCache.bucket_for(
        request, prompt_version="v1", model="gpt-4o-mini"
    ) != EstimationSemanticCache.bucket_for(request, prompt_version="v2", model="gpt-4o-mini")


# --- Lookup -----------------------------------------------------------------


def test_lookup_returns_none_when_index_is_empty() -> None:
    cache, _, _ = _build_cache(hits=[])
    assert cache.lookup(_valid_request(), prompt_version="v1", model="gpt-4o-mini") is None


def test_lookup_returns_none_when_similarity_below_threshold() -> None:
    # Below threshold: cosine distance 0.5 → similarity 0.5 < 0.92
    cache, _, _ = _build_cache(
        threshold=0.92,
        hits=[{"result_json": _canned_result().model_dump_json(), "vector_distance": 0.5}],
    )
    assert cache.lookup(_valid_request(), prompt_version="v1", model="gpt-4o-mini") is None


def test_lookup_returns_result_when_similarity_above_threshold() -> None:
    # Above threshold: distance 0.05 → similarity 0.95
    cache, _, _ = _build_cache(
        threshold=0.92,
        hits=[{"result_json": _canned_result().model_dump_json(), "vector_distance": 0.05}],
    )
    hit = cache.lookup(_valid_request(), prompt_version="v1", model="gpt-4o-mini")
    assert hit is not None
    assert hit.total_cost_eur == 25_000


def test_lookup_log_only_never_serves_even_on_hit() -> None:
    cache, _, _ = _build_cache(
        threshold=0.92,
        log_only=True,
        hits=[{"result_json": _canned_result().model_dump_json(), "vector_distance": 0.01}],
    )
    assert cache.lookup(_valid_request(), prompt_version="v1", model="gpt-4o-mini") is None


# --- Store ------------------------------------------------------------------


def test_store_writes_to_index_with_ttl() -> None:
    cache, fake_index, _ = _build_cache()
    cache.store(_valid_request(), _canned_result(), prompt_version="v1", model="gpt-4o-mini")
    assert fake_index.load.called
    args, kwargs = fake_index.load.call_args
    payload = args[0][0]
    assert payload["bucket"] == "v1:mobile_app:summary:narrative:gpt-4o-mini"
    assert "Standard mobile app" in payload["result_json"]
    assert kwargs.get("ttl") == 60


def test_store_swallows_index_errors() -> None:
    cache, fake_index, _ = _build_cache()
    fake_index.load.side_effect = RuntimeError("redis unreachable")
    # Should not raise — the cache write is best-effort.
    cache.store(_valid_request(), _canned_result(), prompt_version="v1", model="gpt-4o-mini")
