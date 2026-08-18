"""Postgres checkpointer wiring for the estimation graph (Level 2).

The graph persists its state per ``thread_id`` in the SAME project Postgres that
holds the pgvector embeddings — the checkpointer creates its own tables
(``checkpoints``, ``checkpoint_writes``, ``checkpoint_blobs``) and coexists with
them. No new infrastructure.

LangGraph's ``AsyncPostgresSaver`` is built on **psycopg3 (async)**, so it wants a
plain libpq DSN (``postgresql://user:pass@host/db``) — NOT the SQLAlchemy
``postgresql+psycopg://`` / ``+asyncpg`` forms. ``saver_conninfo`` derives it from
the single ``DATABASE_URL`` by stripping the driver token, mirroring
``_async_database_url`` in ``app/foundation/persistence/database.py`` (which swaps
the token for the SQLAlchemy async engine instead).

Live-session change (Session 13): the flow now PAUSES at two human gates and may sit
idle for minutes or days before a resume. A single long-lived connection (the
pre-exercise ``from_conn_string``) can be dropped by the server or a NAT during that
idle, so a resume would hit a dead socket. We back the saver with an
``AsyncConnectionPool`` instead: it validates/reconnects connections on checkout, so
a days-later resume gets a live connection. The saver needs each connection in
``autocommit`` mode with server-side prepares off and a dict row factory — the same
kwargs ``from_conn_string`` set, passed here through the pool.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings, get_settings

log = structlog.get_logger()

# The AsyncPostgresSaver requires connections in this shape (mirrors from_conn_string).
_CONNECTION_KWARGS = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 10


def saver_conninfo(settings: Settings | None = None) -> str:
    """Return a plain libpq DSN for ``AsyncPostgresSaver`` from ``DATABASE_URL``.

    ``postgresql+psycopg://…`` / ``postgresql+asyncpg://…`` → ``postgresql://…``.
    """
    url = (settings or get_settings()).DATABASE_URL
    if "+psycopg" in url:
        return url.replace("+psycopg", "")
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "")
    return url


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Open a pooled ``AsyncPostgresSaver`` over the project Postgres and set it up.

    ``setup()`` is idempotent — it creates the checkpointer tables on first run and
    is a no-op afterwards — so calling it on every startup is safe. Use as an async
    context manager (e.g. entered into the app's ``AsyncExitStack`` in ``lifespan``)
    so the pool is closed on shutdown. The pool reconnects dropped connections, so a
    resume after a long human pause always gets a live connection.
    """
    conninfo = saver_conninfo()
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        min_size=_POOL_MIN_SIZE,
        max_size=_POOL_MAX_SIZE,
        kwargs=_CONNECTION_KWARGS,
        open=False,
    )
    await pool.open(wait=True)
    try:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        log.info("graph_checkpointer_ready", pool_max=_POOL_MAX_SIZE)
        yield checkpointer
    finally:
        await pool.close()
