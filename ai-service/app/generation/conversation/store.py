"""In-memory session store.

A plain ``dict[str, Session]`` indexed by ``session_id``. The class exists
mostly to give the codebase a single seam to swap later (Redis, Postgres, …)
without churning the routers and the service.

Not thread-safe: FastAPI's default ``uvicorn --workers=1`` is fine; with
multiple workers each worker would have its own copy of the store, which
breaks the conversational guarantee. Documented here so the limitation is
visible at the abstraction's surface.
"""

from __future__ import annotations

from app.generation.conversation.models import ConversationHistory, Session


class SessionNotFoundError(KeyError):
    """Raised by ``SessionStore.get_or_404`` when the id is unknown."""


class SessionStore:
    def __init__(self, *, max_turns: int = 6) -> None:
        self._sessions: dict[str, Session] = {}
        self._max_turns = max_turns

    def create(self) -> Session:
        session = Session(history=ConversationHistory(max_turns=self._max_turns))
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_or_404(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(session_id) from exc

    def __len__(self) -> int:
        return len(self._sessions)
