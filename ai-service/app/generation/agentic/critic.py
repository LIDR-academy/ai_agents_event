"""Critic service — independent audit of an EstimationResult.

The Critic is a stateless function: input (transcript, metadata, tier,
estimation) → output ``CriticFeedback``. It does NOT mutate the session and
does NOT make decisions about what to do with its own output — that's the
Boss's job. Keeping responsibilities separate is what makes the Actor-Critic-
Boss pattern auditable.

A failure inside the Critic does NOT block the pipeline. The Boss receives a
synthetic "accept with zero confidence" verdict in that case so the actor's
estimation flows through unmodified — graceful degradation rather than a
hard error in front of the user.
"""

from __future__ import annotations

import structlog

from app.foundation.prompts.loader import render_critic_prompt
from app.domain.schemas.critic import CriticFeedback
from app.domain.schemas.estimation import EstimationResult
from app.generation.conversation.models import ProjectMetadata
from app.generation.conversation.tier_resolver import Tier

log = structlog.get_logger()


class Critic:
    def __init__(self, *, llm_wrapper, model: str) -> None:
        self.llm_wrapper = llm_wrapper
        self.model = model

    def review(
        self,
        *,
        transcript: str,
        metadata: ProjectMetadata,
        tier: Tier,
        result: EstimationResult,
    ) -> CriticFeedback:
        system_prompt, user_message = render_critic_prompt(
            transcript=transcript,
            metadata=metadata,
            tier=tier,
            result=result,
        )

        try:
            feedback, meta = self.llm_wrapper.complete_structured_chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                response_model=CriticFeedback,
                model_override=self.model,
                max_tokens=1500,
                max_retries=3,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "critic_failed_fallback_accept",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            # Degraded: pretend the critic accepted with zero confidence so
            # the Boss returns the actor's output unchanged.
            return CriticFeedback(verdict="accept", issues=[], confidence_in_review=0)

        log.info(
            "critic_completed",
            verdict=feedback.verdict,
            issue_count=len(feedback.issues),
            confidence=feedback.confidence_in_review,
            model=meta.get("model"),
            latency_ms=meta.get("latency_ms"),
        )
        return feedback
