"""HTTP layer for the runtime model configuration (Settings UI).

Thin router: validation of the partial update is the only logic here — the
override store lives in ``app/foundation/llm/runtime_config.py``. Changing a
model takes effect on the NEXT LLM call (wrapper/service read the store per
call); nothing is rebuilt and no restart is needed.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import get_runtime_config, get_runtime_retrieval_config
from app.foundation.llm.runtime_config import (
    AUGMENTATION_KEY,
    HALLUCINATION_GATE_KEY,
    MODEL_KEYS,
    QUERY_TRANSFORM_KEY,
    ROUTING_KEY,
    SYNTHESIS_KEY,
    TEMPORAL_DECAY_KEY,
    RuntimeConfigUnavailable,
    RuntimeModelConfig,
    RuntimeRetrievalConfig,
)
from app.foundation.llm.wrapper import _provider_from_model

log = structlog.get_logger()

# Maps the PUT request field → the Redis hash key for the Session 10 stage toggles.
_STAGE_TOGGLE_KEYS = {
    "routing_enabled": ROUTING_KEY,
    "query_transform_enabled": QUERY_TRANSFORM_KEY,
    "temporal_decay_enabled": TEMPORAL_DECAY_KEY,
    # Session 11 generation-quality toggles.
    "hallucination_gate_enabled": HALLUCINATION_GATE_KEY,
    "augmentation_enabled": AUGMENTATION_KEY,
    "synthesis_enabled": SYNTHESIS_KEY,
}

router = APIRouter(prefix="/api/v1/config", tags=["config"])

EMBEDDING_MODEL_NOTE = "Read-only: changing it would invalidate all stored vectors."

PROVIDER_KEY_FIELDS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class ModelUpdateRequest(BaseModel):
    """Partial update: only the keys present are touched; ``null`` resets."""

    models: dict[str, str | None] = Field(min_length=1)


def _available_models(settings: Settings) -> list[str]:
    """The catalog, filtered by the API keys actually configured."""
    available = []
    for model in settings.AVAILABLE_MODELS:
        key_field = PROVIDER_KEY_FIELDS.get(_provider_from_model(model))
        if key_field and getattr(settings, key_field):
            available.append(model)
    return available


def _config_payload(runtime_config: RuntimeModelConfig, settings: Settings) -> dict:
    return {
        "models": runtime_config.snapshot(),
        "available_models": _available_models(settings),
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_model_note": EMBEDDING_MODEL_NOTE,
    }


@router.get("/models")
def get_models(
    runtime_config: RuntimeModelConfig = Depends(get_runtime_config),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Current model configuration: effective/default/overridden per knob."""
    return _config_payload(runtime_config, settings)


class RetrievalUpdateRequest(BaseModel):
    """Partial update of the Session 10 retrieval toggles. Only the keys present
    are touched; ``null`` resets a knob back to its .env default."""

    search_mode: str | None = Field(default=None, description="'vector' or 'hybrid'.")
    rerank: bool | None = Field(default=None, description="Enable cross-encoder reranking.")
    # Session 10 live: advanced-pipeline stage toggles.
    routing_enabled: bool | None = Field(default=None, description="Enable multi-index routing.")
    query_transform_enabled: bool | None = Field(
        default=None, description="Enable query expansion/decomposition."
    )
    temporal_decay_enabled: bool | None = Field(
        default=None, description="Enable temporal decay re-weighting."
    )
    # Session 11 live: generation-quality stage toggles.
    hallucination_gate_enabled: bool | None = Field(
        default=None, description="Enable the semantic hallucination gate (anchor + judge)."
    )
    augmentation_enabled: bool | None = Field(
        default=None, description="Enable context augmentation (compress + edge-load reorder)."
    )
    synthesis_enabled: bool | None = Field(
        default=None, description="Enable two-stage hours synthesis (range on contradiction)."
    )
    # Session 10 live: per-task hours estimation knobs (numeric).
    task_hours_top_k: int | None = Field(
        default=None, ge=1, le=30, description="Neighbours per task for the hours consensus."
    )
    task_hours_distance_threshold: float | None = Field(
        default=None, ge=0.0, le=2.0, description="Red-flag floor: no match beyond this distance."
    )
    # Session 13 live: the agentic hours-recovery cut. Distinct from the one above —
    # that one decides who gets handed to the recovery agent, this one decides who the
    # agent can actually rescue, and therefore how many tasks end with no hours at all.
    agent_search_distance_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Distance cut for the agentic hours-recovery search.",
    )


@router.get("/retrieval")
def get_retrieval(
    runtime_retrieval: RuntimeRetrievalConfig = Depends(get_runtime_retrieval_config),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Current retrieval configuration: effective/default/overridden per toggle."""
    return {
        "retrieval": runtime_retrieval.snapshot(),
        "reranker_model": settings.RERANKER_MODEL,
    }


@router.put("/retrieval")
def update_retrieval(
    request: RetrievalUpdateRequest,
    runtime_retrieval: RuntimeRetrievalConfig = Depends(get_runtime_retrieval_config),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Flip search mode and/or reranking at runtime — effective on the next
    retrieval, no restart. ``model_fields_set`` distinguishes "reset to default"
    (explicit ``null``) from "leave untouched" (key absent)."""
    sent = request.model_fields_set
    try:
        if "search_mode" in sent:
            try:
                runtime_retrieval.set_search_mode(request.search_mode)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            log.info("runtime_retrieval_changed", key="search_mode", new_value=request.search_mode)
        if "rerank" in sent:
            runtime_retrieval.set_rerank(request.rerank)
            log.info("runtime_retrieval_changed", key="rerank", new_value=request.rerank)
        for field_name, hash_key in _STAGE_TOGGLE_KEYS.items():
            if field_name in sent:
                runtime_retrieval.set_bool(hash_key, getattr(request, field_name))
                log.info(
                    "runtime_retrieval_changed",
                    key=field_name,
                    new_value=getattr(request, field_name),
                )
        if "task_hours_top_k" in sent:
            try:
                runtime_retrieval.set_task_hours_top_k(request.task_hours_top_k)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            log.info(
                "runtime_retrieval_changed",
                key="task_hours_top_k",
                new_value=request.task_hours_top_k,
            )
        if "task_hours_distance_threshold" in sent:
            try:
                runtime_retrieval.set_task_hours_distance_threshold(
                    request.task_hours_distance_threshold
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            log.info(
                "runtime_retrieval_changed",
                key="task_hours_distance_threshold",
                new_value=request.task_hours_distance_threshold,
            )
        if "agent_search_distance_threshold" in sent:
            try:
                runtime_retrieval.set_agent_search_distance_threshold(
                    request.agent_search_distance_threshold
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            log.info(
                "runtime_retrieval_changed",
                key="agent_search_distance_threshold",
                new_value=request.agent_search_distance_threshold,
            )
    except RuntimeConfigUnavailable as exc:
        log.error("runtime_retrieval_write_failed", error=str(exc)[:200])
        raise HTTPException(status_code=503, detail="Runtime config store unavailable") from exc

    return {
        "retrieval": runtime_retrieval.snapshot(),
        "reranker_model": settings.RERANKER_MODEL,
    }


@router.put("/models")
def update_models(
    request: ModelUpdateRequest,
    runtime_config: RuntimeModelConfig = Depends(get_runtime_config),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Apply a partial override update. All-or-nothing: every key/value in the
    payload is validated BEFORE anything is written."""
    available = _available_models(settings)

    # Validate everything first — a bad entry must not half-apply the batch.
    for key, value in request.models.items():
        if key not in MODEL_KEYS:
            raise HTTPException(status_code=422, detail=f"Unknown model key: {key}")
        if value is None:
            continue  # reset is always valid
        if value not in settings.AVAILABLE_MODELS:
            raise HTTPException(status_code=422, detail=f"Model '{value}' is not in the catalog")
        if value not in available:
            key_field = PROVIDER_KEY_FIELDS.get(_provider_from_model(value), "API key")
            raise HTTPException(
                status_code=400,
                detail=f"Model '{value}' requires {key_field}, which is not configured",
            )

    try:
        for key, value in request.models.items():
            old_effective = runtime_config.effective(key)
            runtime_config.set(key, value)
            log.info(
                "runtime_config_changed",
                key=key,
                old_effective=old_effective,
                new_value=value,
                reset=value is None,
            )
    except RuntimeConfigUnavailable as exc:
        log.error("runtime_config_write_failed", error=str(exc)[:200])
        raise HTTPException(status_code=503, detail="Runtime config store unavailable") from exc

    return _config_payload(runtime_config, settings)
