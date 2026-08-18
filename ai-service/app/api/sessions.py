"""Conversational endpoints for Session 5.

Three endpoints:

- ``POST /sessions``                       — create a new session, return its UUID.
- ``POST /sessions/{session_id}/estimate`` — multi-turn estimation. Accepts
  ``multipart/form-data`` with the transcript plus optional file attachments
  (PDF or DOCX). Attachment text is extracted locally (Camino B) and
  concatenated into the transcript before the LLM is invoked.
- ``GET  /sessions/{session_id}``          — debug view of the session
  (metadata + history length). Used by the Rails panel.

Error mapping mirrors the v1 router:
- ``InputGuardrailViolation`` → 400 with ``{reason, message}``.
- ``UnsupportedAttachmentError`` → 415.
- ``AttachmentExtractionError``  → 422.
- ``SessionNotFoundError`` → 404.
- anything else → 502.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.foundation.attachments.extractor import (
    AttachmentExtractionError,
    UnsupportedAttachmentError,
    enrich_transcript,
    extract_text,
)
from app.config import get_settings
from app.dependencies import get_estimation_service, get_session_store
from app.foundation.guardrails.input import InputGuardrailViolation
from app.domain.schemas.estimation import (
    ACBResponse,
    DetailLevel,
    EstimationResponse,
    OutputFormat,
    ProjectType,
)
from app.domain.estimation_service import EstimationService
from app.generation.conversation.models import ProjectMetadata, Session
from app.generation.conversation.store import SessionNotFoundError, SessionStore
from app.generation.conversation.tier_resolver import Tier

log = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionResponse(BaseModel):
    session_id: str = Field(description="UUID identifier for the new conversational session.")


class SessionInfoResponse(BaseModel):
    session_id: str
    message_count: int
    max_turns: int
    metadata: ProjectMetadata
    anchors_count: int = 0
    summary_chars: int = 0
    last_resolved_tier: str | None = None
    last_tier_rule: str | None = None


@router.post("", response_model=CreateSessionResponse, status_code=201)
def create_session(
    store: SessionStore = Depends(get_session_store),
) -> CreateSessionResponse:
    session = store.create()
    log.info("session_created", session_id=session.session_id)
    return CreateSessionResponse(session_id=session.session_id)


@router.get("/{session_id}", response_model=SessionInfoResponse)
def get_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
) -> SessionInfoResponse:
    try:
        session = store.get_or_404(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    return SessionInfoResponse(
        session_id=session.session_id,
        message_count=len(session.history.messages),
        max_turns=session.history.max_turns,
        metadata=session.metadata,
        anchors_count=len(session.history.anchors),
        summary_chars=len(session.history.summary or ""),
        last_resolved_tier=session.last_resolved_tier,
        last_tier_rule=session.last_tier_rule,
    )


async def _resolve_session_and_enrich(
    session_id: str,
    transcript: str,
    attachments: list[UploadFile],
    store: SessionStore,
) -> tuple[Session, str, int]:
    """Shared prelude for both /estimate and /estimate-acb.

    Returns ``(session, enriched_transcript, attachments_total_chars)``. The
    third element is the sum of raw extracted text across all attachments
    (excluding the ``--- attachment: ... ---`` fences added by
    ``enrich_transcript``); the stress runner uses it to feed the
    ``attachments_total_chars`` field of ``TurnObservation`` without having
    to re-do the math. Raises ``HTTPException`` for session/attachment
    problems; the caller wraps the LLM call separately.
    """
    try:
        session = store.get_or_404(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc

    settings = get_settings()
    extracted: list[tuple[str, str]] = []
    for upload in attachments or []:
        if not upload.filename:
            continue
        content = await upload.read()
        try:
            text = extract_text(
                filename=upload.filename,
                content=content,
                max_chars=settings.MAX_ATTACHMENT_CHARS,
            )
        except UnsupportedAttachmentError as exc:
            raise HTTPException(
                status_code=415,
                detail={"reason": "unsupported_attachment", "filename": exc.filename},
            ) from exc
        except AttachmentExtractionError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "reason": "attachment_extraction_failed",
                    "filename": exc.filename,
                    "message": exc.message,
                },
            ) from exc
        if text:
            extracted.append((upload.filename, text))

    enriched = enrich_transcript(transcript=transcript, attachments=extracted)
    attachments_total_chars = sum(len(text) for _, text in extracted)
    log.info(
        "session_estimate_received",
        session_id=session_id,
        transcript_chars=len(transcript),
        enriched_transcript_chars=len(enriched),
        attachment_count=len(extracted),
        attachments_total_chars=attachments_total_chars,
    )
    return session, enriched, attachments_total_chars


def _map_pipeline_errors(exc: Exception) -> HTTPException:
    """Same mapping for both endpoints: input guardrail → 400, else → 502."""
    if isinstance(exc, InputGuardrailViolation):
        log.info(
            "session_estimate_blocked_by_input_guardrail",
            reason=exc.reason,
            message=exc.message,
        )
        return HTTPException(status_code=400, detail={"reason": exc.reason, "message": exc.message})
    log.error(
        "session_estimate_endpoint_error",
        error=str(exc)[:400],
        error_type=type(exc).__name__,
    )
    return HTTPException(status_code=502, detail="Upstream LLM call failed")


@router.post("/{session_id}/estimate", response_model=EstimationResponse)
async def estimate_in_session(
    session_id: str,
    transcript: str = Form(..., min_length=20, max_length=80_000),
    project_type: ProjectType = Form(...),
    detail_level: DetailLevel = Form(...),
    output_format: OutputFormat = Form(...),
    tier: Tier | None = Form(default=None),
    attachments: list[UploadFile] = File(default_factory=list),
    store: SessionStore = Depends(get_session_store),
    service: EstimationService = Depends(get_estimation_service),
) -> EstimationResponse:
    session, enriched, attachments_total_chars = await _resolve_session_and_enrich(
        session_id, transcript, attachments, store
    )
    try:
        return service.estimate_conversational(
            session=session,
            transcript=enriched,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
            tier=tier,
            attachments_total_chars=attachments_total_chars,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_pipeline_errors(exc) from exc


@router.post("/{session_id}/estimate-acb", response_model=ACBResponse)
async def estimate_in_session_acb(
    session_id: str,
    transcript: str = Form(..., min_length=20, max_length=80_000),
    project_type: ProjectType = Form(...),
    detail_level: DetailLevel = Form(...),
    output_format: OutputFormat = Form(...),
    tier: Tier | None = Form(default=None),
    attachments: list[UploadFile] = File(default_factory=list),
    store: SessionStore = Depends(get_session_store),
    service: EstimationService = Depends(get_estimation_service),
) -> ACBResponse:
    """Actor-Critic-Boss variant of /estimate.

    Same multipart contract; the response carries an ``acb`` field with the
    iteration trail (verdict, confidence, issues per round) so callers can
    show the audit trail in their UI.
    """
    session, enriched, _attachments_total_chars = await _resolve_session_and_enrich(
        session_id, transcript, attachments, store
    )
    try:
        return service.estimate_with_acb(
            session=session,
            transcript=enriched,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
            tier=tier,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_pipeline_errors(exc) from exc
