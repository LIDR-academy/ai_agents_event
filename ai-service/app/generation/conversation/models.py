"""In-process data structures for the conversational estimator.

Design notes
------------
- ``ConversationHistory.to_messages()`` returns the LLM-ready ``messages``
  array (the ``role``/``content`` dicts expected by both OpenAI and Anthropic
  through LiteLLM + Instructor). The system prompt is supplied at call time
  because it is regenerated from the current ``ProjectMetadata`` each turn —
  it is NOT stored as a fixed entry in the history.
- ``ProjectMetadata`` uses ``Optional`` fields so an empty metadata block is a
  legitimate first-turn state. Merging is additive for the technology list and
  overwrite for scalar fields (see ``merge_with``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


Role = Literal["user", "assistant"]


class Message(BaseModel):
    """One message in the conversation history. The system prompt is NOT a
    Message — it lives outside the history because it is re-rendered each
    turn from the current ``ProjectMetadata``."""

    role: Role
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationHistory(BaseModel):
    """A sliding window of user/assistant pairs, augmented with summary + anchors.

    Three storage slots:

    - ``messages``: the recent sliding window (last ``max_turns`` pairs).
    - ``anchors``: turns flagged by ``AnchorDetector`` as durable commitments
      (signed NDA, frozen scope, compliance context, …). Anchors live outside
      the sliding window and are never evicted by it.
    - ``summary``: a free-text cumulative summary of older non-anchor turns
      that have already been folded away by the ``CompressionPolicy``.

    ``to_messages()`` composes the three into the array the LLM sees:

        [summary_envelope?] + anchors_in_order + recent_sliding_window
    """

    max_turns: int = Field(default=6, ge=1)
    messages: list[Message] = Field(default_factory=list)
    anchors: list[Message] = Field(default_factory=list)
    summary: str | None = Field(default=None)

    def append(self, *, user: str, assistant: str) -> None:
        """Add one turn (user message + assistant message).

        Trim / anchor promotion / cumulative summary are NOT applied here —
        that's the job of ``CompressionPolicy.apply`` (and its wrapper
        ``apply_compression``). The service layer is responsible for invoking
        compression after each append. Keeping the data structure dumb means
        the policy is the single source of truth for the sliding window,
        anchors and summary — there is exactly one place where the LLM is
        called to decide what to forget.
        """
        self.messages.append(Message(role="user", content=user))
        self.messages.append(Message(role="assistant", content=assistant))

    def to_messages(self) -> list[dict[str, str]]:
        """Return the ``messages`` array ready to splice into the LLM call.

        Order is intentional: the summary anchors the model in the early
        conversation, anchors carry the irreducible commitments verbatim, and
        the recent window provides the active turn-by-turn context. The caller
        prepends a fresh ``system`` prompt on top.
        """
        out: list[dict[str, str]] = []
        if self.summary:
            # Wrap as a synthetic user message — most providers route this
            # cleanly through any tool-use scaffolding, and the [Earlier...]
            # bracket marks it visually so the model treats it as context.
            out.append(
                {
                    "role": "user",
                    "content": (
                        "[Earlier conversation summary — the recent turns "
                        "below are the live thread]\n" + self.summary
                    ),
                }
            )
        for anchor in self.anchors:
            out.append({"role": anchor.role, "content": anchor.content})
        for m in self.messages:
            out.append({"role": m.role, "content": m.content})
        return out

    def _trim(self) -> None:
        max_messages = self.max_turns * 2
        overflow = len(self.messages) - max_messages
        if overflow > 0:
            # Drop in pairs so role alternation stays intact.
            if overflow % 2 != 0:
                overflow += 1
            del self.messages[:overflow]


class ProjectMetadata(BaseModel):
    """Facts about the project under discussion, kept across turns.

    All fields are optional: on the first turn the LLM has not yet committed
    to a project name, team size, technology list or scope. The metadata
    extractor populates them turn by turn.
    """

    project_name: str | None = Field(default=None, max_length=120)
    assumed_team_size: int | None = Field(default=None, ge=1, le=50)
    mentioned_technologies: list[str] = Field(default_factory=list)
    agreed_scope: str | None = Field(default=None, max_length=2000)

    def is_empty(self) -> bool:
        return (
            self.project_name is None
            and self.assumed_team_size is None
            and not self.mentioned_technologies
            and self.agreed_scope is None
        )

    def merge_with(self, update: "ProjectMetadata") -> "ProjectMetadata":
        """Return a new metadata where non-null fields from ``update`` win,
        and ``mentioned_technologies`` is the case-insensitive union.

        Scalar overwrite + list union is the right default here: the extractor
        is allowed to refine the project name or team size as the conversation
        clarifies them, but technologies accumulate (the user adding a new
        stack item shouldn't make the previous ones disappear).
        """
        merged_tech = list(self.mentioned_technologies)
        seen = {t.lower() for t in merged_tech}
        for tech in update.mentioned_technologies:
            if tech.lower() not in seen:
                merged_tech.append(tech)
                seen.add(tech.lower())

        return ProjectMetadata(
            project_name=update.project_name or self.project_name,
            assumed_team_size=update.assumed_team_size or self.assumed_team_size,
            mentioned_technologies=merged_tech,
            agreed_scope=update.agreed_scope or self.agreed_scope,
        )


class Session(BaseModel):
    """A conversational estimation session.

    Lives in memory of the FastAPI process. State is lost on restart, which is
    intentional for the exercise — persistence enters with the live session's
    advanced topics.

    ``last_resolved_tier`` and ``last_tier_rule`` cache the most recent tier
    resolution so the GET /sessions/{id} endpoint can show the side panel
    information without re-running the resolver.
    """

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    history: ConversationHistory = Field(default_factory=ConversationHistory)
    metadata: ProjectMetadata = Field(default_factory=ProjectMetadata)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_resolved_tier: str | None = None
    last_tier_rule: str | None = None
