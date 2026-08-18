"""Hybrid memory compression for long conversations.

Three pieces work together:

- ``AnchorDetector`` decides whether a turn carries durable information the
  conversation should never forget (signed NDA, frozen scope, agreed budget,
  regulatory mention). Anchors are excluded from eviction.
- ``CumulativeSummarizer`` folds older non-anchor turns into a running
  free-text summary, kept on the ``ConversationHistory``.
- ``CompressionPolicy`` is the orchestrator: it inspects the history after
  each ``append`` and decides what (if anything) to compress.

The output of ``to_messages()`` after compression is:

    [synthetic_summary?] + anchors_in_order + recent_sliding_window
"""

from app.generation.conversation.compression.anchors import AnchorDetector, AnchorMatch
from app.generation.conversation.compression.policy import CompressionPolicy, apply_compression
from app.generation.conversation.compression.summarizer import CumulativeSummarizer

__all__ = [
    "AnchorDetector",
    "AnchorMatch",
    "CompressionPolicy",
    "CumulativeSummarizer",
    "apply_compression",
]
