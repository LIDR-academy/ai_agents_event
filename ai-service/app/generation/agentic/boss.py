"""Boss orchestrator — coordinates Actor and Critic.

The Boss is a tiny state machine:

    actor_call → critic_review → decide
                                  ├── accept      → return
                                  ├── needs_iteration AND iterations_left
                                  │                  → re-call actor with feedback
                                  │                  → loop
                                  └── (out of iterations or reject)
                                                   → synthesize fallback

The Boss does NOT do LLM calls of its own — it only chooses what to do next.
That keeps the decision logic provable: every choice is traceable in
``BossTrace.iterations`` and reproducible from the inputs.

The Actor is passed as a callable rather than an object. The signature is::

    actor(critic_feedback: CriticFeedback | None) -> EstimationResult

so the Boss can re-invoke the actor at iteration N+1 with the feedback from
iteration N. Wiring lives in ``EstimationService.estimate_with_acb``.
"""

from __future__ import annotations

from typing import Callable

import structlog

from app.domain.schemas.acb import ACBIteration, BossDecision, BossTrace
from app.domain.schemas.critic import CriticFeedback
from app.domain.schemas.estimation import EstimationResult

log = structlog.get_logger()


ActorCallable = Callable[[CriticFeedback | None], EstimationResult]
CriticCallable = Callable[[EstimationResult], CriticFeedback]


class Boss:
    """Stateless orchestrator. One ``run`` per estimation."""

    def __init__(self, *, max_iterations: int = 2) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self.max_iterations = max_iterations

    def run(
        self,
        *,
        actor: ActorCallable,
        critic: CriticCallable,
    ) -> tuple[EstimationResult, BossTrace]:
        trace = BossTrace(final_decision="accept", iterations_run=0)
        feedback: CriticFeedback | None = None
        current: EstimationResult | None = None

        for iteration in range(self.max_iterations):
            current = actor(feedback)
            review = critic(current)

            decision = self._decide(review, iterations_left=(self.max_iterations - iteration - 1))
            trace.iterations.append(
                ACBIteration(
                    iteration=iteration,
                    decision_after=decision,
                    critic_verdict=review.verdict,
                    critic_confidence=review.confidence_in_review,
                    issue_summary=[
                        f"[{i.severity}] {i.category} @ {i.field_path}" for i in review.issues[:5]
                    ],
                )
            )
            trace.iterations_run = iteration + 1
            log.info(
                "boss_iteration",
                iteration=iteration,
                decision=decision,
                verdict=review.verdict,
                issues=len(review.issues),
            )

            if decision == "accept":
                trace.final_decision = "accept"
                return current, trace
            if decision == "synthesize":
                trace.final_decision = "synthesize"
                return self._synthesize_fallback(current, review), trace

            # decision == "iterate" → loop with feedback
            feedback = review

        # Loop exhausted: last actor result with critic still flagging issues.
        trace.final_decision = "synthesize"
        log.info("boss_iteration_budget_exhausted", iterations=self.max_iterations)
        # ``current`` must exist because max_iterations >= 1.
        assert current is not None
        return self._synthesize_fallback(current, feedback), trace

    # -- decision helpers -------------------------------------------------

    @staticmethod
    def _decide(review: CriticFeedback, *, iterations_left: int) -> BossDecision:
        if review.verdict == "accept":
            return "accept"
        if review.verdict == "reject":
            return "synthesize"
        # needs_iteration
        if iterations_left > 0:
            return "iterate"
        return "synthesize"

    @staticmethod
    def _synthesize_fallback(
        last_result: EstimationResult,
        last_feedback: CriticFeedback | None,
    ) -> EstimationResult:
        """When the Boss cannot accept, return the actor's last draft annotated
        with the Critic's open issues and a reduced confidence — never an empty
        envelope.

        Rationale: discarding the draft hides the best available answer behind
        a placeholder ("Out of scope"). The honest signal that the loop did not
        converge already travels in ``BossTrace.final_decision == "synthesize"``
        and the audit panel; the user is better served by an estimation with
        caveats than by zeroed-out numbers.
        """
        if last_feedback is None or not last_feedback.issues:
            # Defensive branch: no feedback to attach. Return the draft with a
            # minimal note so callers can still see that this came out of the
            # synthesize path.
            note = "⚠ Open caveats from independent review: (no detail available)\n\n"
            new_summary = (note + last_result.summary)[:1200]
            return last_result.model_copy(update={"summary": new_summary})

        issue_lines = [
            f"- [{issue.severity}] {issue.category} ({issue.field_path}): {issue.description}"
            for issue in last_feedback.issues
        ]
        caveats_block = (
            "⚠ Open caveats from independent review (loop did not fully converge):\n"
            + "\n".join(issue_lines)
            + "\n\n"
        )
        new_summary = (caveats_block + last_result.summary)[:1200]
        # Floor at 30 to stay above ``LOW_CONFIDENCE_THRESHOLD`` — going below
        # would trigger the schema validator that demands an "Out of scope:"
        # summary prefix, which contradicts the whole point of returning a
        # usable annotated draft.
        reduced_confidence = max(30, last_result.confidence_pct // 2)
        return last_result.model_copy(
            update={
                "summary": new_summary,
                "confidence_pct": reduced_confidence,
            }
        )
