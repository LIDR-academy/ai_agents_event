"""Unit tests for ``CompressionPolicy`` and the ``apply_compression`` wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.generation.conversation.compression.anchors import AnchorDetector
from app.generation.conversation.compression.policy import CompressionPolicy, apply_compression
from app.generation.conversation.compression.summarizer import CumulativeSummarizer, _SummaryEnvelope
from app.generation.conversation.models import ConversationHistory


def _make_wrapper_returning(summary_text: str = "Running summary text."):
    wrapper = MagicMock()
    wrapper.complete_structured_chat.return_value = (
        _SummaryEnvelope(summary=summary_text),
        {"model": "gpt-4o-mini", "provider": "openai", "latency_ms": 1},
    )
    return wrapper


def test_no_op_when_window_under_cap() -> None:
    history = ConversationHistory(max_turns=3)
    history.append(user="hi", assistant="hello")
    history.append(user="and again", assistant="sure")

    wrapper = _make_wrapper_returning()
    policy = CompressionPolicy(
        anchor_detector=AnchorDetector(mode="heuristic"),
        summarizer=CumulativeSummarizer(llm_wrapper=wrapper, model="gpt-4o-mini"),
    )
    policy.apply(history)

    # Below cap → no summarizer call, no anchors promoted, no summary built.
    wrapper.complete_structured_chat.assert_not_called()
    assert history.summary is None
    assert history.anchors == []


def test_non_anchor_overflow_feeds_summarizer() -> None:
    history = ConversationHistory(max_turns=2)
    history.append(user="t1u plain stack discussion", assistant="t1a")
    history.append(user="t2u still plain", assistant="t2a")
    history.append(user="t3u still plain", assistant="t3a")  # forces overflow

    wrapper = _make_wrapper_returning("compressed: t1+t2 absorbed")
    policy = CompressionPolicy(
        anchor_detector=AnchorDetector(mode="heuristic"),
        summarizer=CumulativeSummarizer(llm_wrapper=wrapper, model="gpt-4o-mini"),
    )
    policy.apply(history)

    assert history.summary == "compressed: t1+t2 absorbed"
    assert history.anchors == []
    # max_turns=2 → cap is 4 messages. After evicting t1's pair, the two
    # most recent pairs (t2, t3) remain verbatim in the window.
    assert len(history.messages) == 4
    assert history.messages[0].content == "t2u still plain"
    assert history.messages[-1].content == "t3a"


def test_anchor_overflow_promotes_pair_and_skips_summary() -> None:
    history = ConversationHistory(max_turns=2)
    history.append(user="t1u We signed the NDA last week.", assistant="t1a acknowledged")
    history.append(user="t2u plain follow up", assistant="t2a")
    history.append(user="t3u plain", assistant="t3a")  # overflows; t1 must promote

    wrapper = _make_wrapper_returning("(should not be called)")
    policy = CompressionPolicy(
        anchor_detector=AnchorDetector(mode="heuristic"),
        summarizer=CumulativeSummarizer(llm_wrapper=wrapper, model="gpt-4o-mini"),
    )
    policy.apply(history)

    # t1 is an anchor → promoted; summarizer never runs.
    assert len(history.anchors) == 2
    assert "NDA" in history.anchors[0].content
    assert history.summary is None
    wrapper.complete_structured_chat.assert_not_called()
    # Window now holds t2 + t3.
    assert [m.content for m in history.messages] == [
        "t2u plain follow up",
        "t2a",
        "t3u plain",
        "t3a",
    ]


def test_to_messages_composes_summary_anchors_recent() -> None:
    history = ConversationHistory(max_turns=2)
    history.append(user="t1u We signed the NDA last week.", assistant="t1a")
    history.append(user="t2u plain", assistant="t2a")
    history.append(user="t3u plain", assistant="t3a")

    wrapper = _make_wrapper_returning("compressed text")
    apply_compression(
        history,
        llm_wrapper=wrapper,
        compression_model="gpt-4o-mini",
        anchor_detection_mode="heuristic",
    )

    messages = history.to_messages()
    # Layout: [summary?] + anchors + recent.
    # In this scenario t1 was an anchor (the only overflow), so no summary.
    assert history.summary is None
    assert messages[0]["role"] == "user"
    assert "NDA" in messages[0]["content"]
    # Recent window comes after the anchors.
    contents = [m["content"] for m in messages]
    assert contents[-2:] == ["t3u plain", "t3a"]


def test_repeated_apply_is_idempotent_when_no_new_messages() -> None:
    history = ConversationHistory(max_turns=2)
    for n in range(5):
        history.append(user=f"t{n} plain talk about stacks", assistant=f"a{n}")

    wrapper = _make_wrapper_returning("first compression")
    apply_compression(
        history,
        llm_wrapper=wrapper,
        compression_model="gpt-4o-mini",
        anchor_detection_mode="heuristic",
    )

    summary_after_first = history.summary
    messages_after_first = list(history.messages)
    wrapper.complete_structured_chat.reset_mock()

    apply_compression(
        history,
        llm_wrapper=wrapper,
        compression_model="gpt-4o-mini",
        anchor_detection_mode="heuristic",
    )

    assert history.summary == summary_after_first
    assert history.messages == messages_after_first
    wrapper.complete_structured_chat.assert_not_called()


def test_summarizer_failure_keeps_previous_summary() -> None:
    history = ConversationHistory(max_turns=2)
    history.summary = "Existing summary stays."
    for n in range(3):
        history.append(user=f"t{n} plain", assistant=f"a{n}")

    wrapper = MagicMock()
    wrapper.complete_structured_chat.side_effect = RuntimeError("LLM down")

    apply_compression(
        history,
        llm_wrapper=wrapper,
        compression_model="gpt-4o-mini",
        anchor_detection_mode="heuristic",
    )
    # Summary unchanged after failure; the sliding window still got trimmed.
    assert history.summary == "Existing summary stays."
    assert len(history.messages) <= history.max_turns * 2
