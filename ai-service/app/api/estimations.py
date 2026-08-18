"""POST /api/v1/estimate — typed input, validated structured output.

Error handling mapping:

- ``InputGuardrailViolation`` → HTTP 400 with ``{reason, message}`` so the cliente
  can render a clear actionable message (the regex caught a prompt injection,
  PII, or moderation flagged the content).
- Anything else from the pipeline → HTTP 502 (the LLM upstream failed,
  including ``InstructorRetryException`` when the model couldn't satisfy
  validators within ``max_retries``).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_estimation_service
from app.foundation.guardrails.input import InputGuardrailViolation
from app.domain.schemas.estimation import EstimationRequest, EstimationResponse
from app.domain.estimation_service import EstimationService

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
def create_estimation(
    request: EstimationRequest,
    service: EstimationService = Depends(get_estimation_service),
) -> EstimationResponse:
    """Run the full estimation pipeline and return the structured response."""
    log.info(
        "estimation_request_received",
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
        description_chars=len(request.description),
    )

    try:
        return service.estimate(request)
    except InputGuardrailViolation as exc:
        log.info(
            "estimation_blocked_by_input_guardrail",
            reason=exc.reason,
            message=exc.message,
        )
        raise HTTPException(
            status_code=400, detail={"reason": exc.reason, "message": exc.message}
        ) from exc
    except Exception as exc:
        log.error(
            "estimation_endpoint_error",
            error=str(exc)[:400],
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Upstream LLM call failed") from exc
