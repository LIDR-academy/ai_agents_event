#!/bin/sh
# =============================================================================
# Entrypoint for the AI service (Session 15)
# =============================================================================
# Applies pending Alembic migrations, then execs whatever CMD was given.
#
# Why here and not in the compose ``command:`` (which is where it lived until
# S14): the strict docker-compose.yml no longer overrides the command, and the
# image has to be deployable on its own. Anything that must happen before the
# server accepts traffic belongs in the entrypoint, so `docker run`, compose and
# a cloud runtime all behave identically.
#
# ``exec "$@"`` matters: it replaces this shell with uvicorn so the server
# becomes PID 1 and receives SIGTERM directly. Without it, docker stop would
# hit the shell and the app would never shut down gracefully.
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] applying database migrations..."
    alembic upgrade head
    echo "[entrypoint] migrations up to date."
else
    echo "[entrypoint] RUN_MIGRATIONS=false — skipping alembic."
fi

exec "$@"
