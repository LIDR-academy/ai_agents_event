"""When to compress, what to compress, and what to keep verbatim.

The policy is the only piece that mutates the ``ConversationHistory`` beyond
``append``. It runs after each turn (see ``EstimationService.estimate_conversational``)
with this loop, in order:

1. While the sliding window is over the cap (``max_turns * 2`` messages),
   peel the oldest user/assistant pair off the front.
2. Send the user side of the pair to the ``AnchorDetector``. If it's an
   anchor, move BOTH messages of the pair into ``history.anchors`` so the
   commitment survives verbatim. Otherwise, queue both for the summarizer.
3. After the loop, if any non-anchor pairs were peeled off, hand them to
   the ``CumulativeSummarizer`` together with the previous summary, and
   replace ``history.summary`` with the new running text.

The policy is intentionally idempotent: a second call with no change to
``messages`` is a no-op.
"""

from __future__ import annotations

import structlog

from app.generation.conversation.compression.anchors import AnchorDetector
from app.generation.conversation.compression.summarizer import CumulativeSummarizer
from app.generation.conversation.models import ConversationHistory, Message

log = structlog.get_logger()


class CompressionPolicy:
    def __init__(
        self,
        *,
        anchor_detector: AnchorDetector,
        summarizer: CumulativeSummarizer,
    ) -> None:
        self.anchor_detector = anchor_detector
        self.summarizer = summarizer

    def should_compress(self, history: ConversationHistory) -> bool:
        return len(history.messages) > history.max_turns * 2

    def apply(self, history: ConversationHistory) -> None:
        """Mutate ``history`` in place: promote anchors and absorb the rest
        into the running summary. No-op when the window is under the cap."""

        if not self.should_compress(history):
            return

        evicted_for_summary: list[Message] = []
        promoted_anchor_rules: list[list[str]] = []

        while len(history.messages) > history.max_turns * 2:
            # Pair-safe pop: with even-length messages and alternating roles,
            # the two oldest entries are the user/assistant pair to retire.
            if len(history.messages) < 2:
                break
            user_msg = history.messages[0]
            assistant_msg = history.messages[1]

            match = self.anchor_detector.detect(user_msg)
            if match.is_anchor:
                history.anchors.append(user_msg)
                history.anchors.append(assistant_msg)
                promoted_anchor_rules.append(match.matched_rules)
            else:
                evicted_for_summary.append(user_msg)
                evicted_for_summary.append(assistant_msg)

            del history.messages[:2]

        if evicted_for_summary:
            history.summary = self.summarizer.summarize(
                previous_summary=history.summary,
                evicted=evicted_for_summary,
            )

        log.info(
            "history_compressed",
            promoted_anchors=len(promoted_anchor_rules),
            anchor_rules=[r for rules in promoted_anchor_rules for r in rules],
            evicted_to_summary=len(evicted_for_summary),
            summary_chars=len(history.summary or ""),
            anchors_count=len(history.anchors),
            recent_messages=len(history.messages),
        )


def apply_compression(
    history: ConversationHistory,
    *,
    llm_wrapper,
    compression_model: str,
    anchor_detection_mode: str = "heuristic",
) -> None:
    """Convenience wrapper used by ``EstimationService``.

    Builds the detector + summarizer with default wiring and runs the policy
    once. Kept narrow so the service doesn't have to know about the inner
    structure of the compression module.
    """
    detector = AnchorDetector(
        mode=anchor_detection_mode,  # type: ignore[arg-type]
        llm_wrapper=llm_wrapper,
        llm_model=compression_model,
    )
    summarizer = CumulativeSummarizer(llm_wrapper=llm_wrapper, model=compression_model)
    policy = CompressionPolicy(anchor_detector=detector, summarizer=summarizer)
    policy.apply(history)
