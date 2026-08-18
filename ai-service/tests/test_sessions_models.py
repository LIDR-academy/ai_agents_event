"""Unit tests for ConversationHistory, ProjectMetadata and SessionStore."""

from __future__ import annotations

import pytest

from app.generation.conversation.models import ConversationHistory, ProjectMetadata, Session
from app.generation.conversation.store import SessionNotFoundError, SessionStore


def test_history_append_does_not_auto_trim() -> None:
    """As of Session 5 live, append is a pure data operation. Compression is
    the single source of truth for the window — see CompressionPolicy."""
    history = ConversationHistory(max_turns=2)
    history.append(user="t1u", assistant="t1a")
    history.append(user="t2u", assistant="t2a")
    history.append(user="t3u", assistant="t3a")

    messages = history.to_messages()
    assert len(messages) == 6
    assert messages[0] == {"role": "user", "content": "t1u"}
    assert messages[-1] == {"role": "assistant", "content": "t3a"}


def test_history_compression_drops_oldest_non_anchor_pairs() -> None:
    """The sliding-window invariant is now enforced by CompressionPolicy."""
    from unittest.mock import MagicMock
    from app.generation.conversation.compression import apply_compression
    from app.generation.conversation.compression.summarizer import _SummaryEnvelope

    wrapper = MagicMock()
    wrapper.complete_structured_chat.return_value = (
        _SummaryEnvelope(summary="summary text"),
        {"model": "gpt-4o-mini", "provider": "openai", "latency_ms": 1},
    )

    history = ConversationHistory(max_turns=2)
    history.append(user="t1u plain", assistant="t1a")
    history.append(user="t2u plain", assistant="t2a")
    history.append(user="t3u plain", assistant="t3a")

    apply_compression(
        history,
        llm_wrapper=wrapper,
        compression_model="gpt-4o-mini",
        anchor_detection_mode="heuristic",
    )

    # Last 2 pairs verbatim + a summary user message at the front, no anchors.
    messages = history.to_messages()
    assert messages[0]["role"] == "user"
    assert "Earlier conversation summary" in messages[0]["content"]
    assert messages[-1] == {"role": "assistant", "content": "t3a"}
    assert len(history.messages) == 4
    assert history.anchors == []


def test_history_to_messages_excludes_system_prompt() -> None:
    history = ConversationHistory(max_turns=3)
    history.append(user="hi", assistant="hello")
    messages = history.to_messages()
    assert all(m["role"] in {"user", "assistant"} for m in messages)


def test_project_metadata_is_empty_by_default() -> None:
    metadata = ProjectMetadata()
    assert metadata.is_empty()
    assert metadata.mentioned_technologies == []


def test_project_metadata_merge_overwrites_scalars_and_unions_techs() -> None:
    base = ProjectMetadata(
        project_name="Nimbus CRM",
        assumed_team_size=3,
        mentioned_technologies=["React", "Postgres"],
        agreed_scope="Phase 1 only",
    )
    update = ProjectMetadata(
        project_name="Nimbus CRM v2",
        assumed_team_size=None,
        mentioned_technologies=["postgres", "Redis"],
        agreed_scope=None,
    )
    merged = base.merge_with(update)

    assert merged.project_name == "Nimbus CRM v2"  # overwritten
    assert merged.assumed_team_size == 3  # preserved (update was None)
    assert merged.agreed_scope == "Phase 1 only"  # preserved
    # Case-insensitive union, original order preserved
    assert merged.mentioned_technologies == ["React", "Postgres", "Redis"]


def test_session_store_create_returns_unique_ids() -> None:
    store = SessionStore()
    s1 = store.create()
    s2 = store.create()
    assert s1.session_id != s2.session_id
    assert len(store) == 2


def test_session_store_get_or_404_raises_for_unknown() -> None:
    store = SessionStore()
    with pytest.raises(SessionNotFoundError):
        store.get_or_404("nope")


def test_session_store_respects_configured_max_turns() -> None:
    store = SessionStore(max_turns=4)
    session = store.create()
    assert session.history.max_turns == 4


def test_session_round_trip_through_json() -> None:
    """Pydantic serialisation works — useful for the GET /sessions/{id} debug endpoint."""
    session = Session()
    session.history.append(user="hi", assistant="hello")
    session.metadata = ProjectMetadata(project_name="Foo")
    raw = session.model_dump(mode="json")
    restored = Session.model_validate(raw)
    assert restored.metadata.project_name == "Foo"
    assert len(restored.history.messages) == 2
