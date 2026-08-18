# =============================================================================
# Stage 1 — Builder
# =============================================================================
# We use a multi-stage build so that build tools (uv, compilers, headers) stay
# out of the final image, keeping it small and reducing the attack surface.
FROM python:3.11-slim AS builder

# Install uv — a fast Python package manager written in Rust.
# Copying the static binary from the official image is the fastest method.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first. Docker caches each layer, so dependencies
# are only reinstalled when pyproject.toml or uv.lock actually change.
COPY pyproject.toml uv.lock* ./

# Install production dependencies only (no dev tools like pytest/ruff).
# --no-install-project skips installing our own package (we just need deps).
# uv will generate the lockfile if it doesn't exist yet.
RUN uv sync --no-install-project --no-dev


# =============================================================================
# Stage 2 — Runtime
# =============================================================================
# Start from a clean slim image — no build tools, no uv, no cached layers
# from the builder stage. Only the virtual environment is carried over.
FROM python:3.11-slim AS runtime

# Never run containers as root. Create a dedicated user and group so the
# process has minimal filesystem and OS-level privileges.
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home appuser

WORKDIR /app

# Bring in the pre-built virtual environment from the builder stage.
COPY --from=builder /app/.venv /app/.venv

# Copy application source code.
#
# Session 15: the image is now SELF-CONTAINED. Until S14 only ``app/`` was
# copied and everything else (alembic, scripts, data) arrived through the
# compose bind mounts — which meant the image could not apply its own
# migrations and was undeployable anywhere without the source tree next to it.
# In cloud there is no bind mount, so all the runtime inputs ship inside:
#   - alembic/ + alembic.ini → the entrypoint runs ``alembic upgrade head``
#   - pyproject.toml         → alembic's ``prepend_sys_path = .`` + tooling config
#   - scripts/               → corpus builders and the S12/S13/S14 demo runners
#   - data/                  → the seed corpora (~700 KB, cheap to bake in)
COPY app/       /app/app/
COPY alembic/   /app/alembic/
COPY scripts/   /app/scripts/
COPY data/      /app/data/
COPY alembic.ini pyproject.toml /app/
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# Ensure the non-root user owns everything it needs to run.
RUN chmod +x /app/docker-entrypoint.sh && chown -R appuser:appgroup /app

# Put the virtual environment's bin directory first on PATH so `python` and
# `uvicorn` resolve to the versions installed inside the venv.
ENV PATH="/app/.venv/bin:$PATH"
# Print logs immediately instead of buffering — critical for container logging.
ENV PYTHONUNBUFFERED=1
# Don't generate .pyc files at runtime; they add no value inside a container.
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to the non-root user for all subsequent commands and at runtime.
USER appuser

EXPOSE 8000

# Docker-native health check. The orchestrator (Compose, Swarm, K8s) will
# mark the container as unhealthy if this probe fails consecutively.
#
# The probe hits /health, which is deliberately exempt from the service-token
# check (app/api/service_token.py) and touches neither the LLM nor the DB.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

# Migrations run in the entrypoint, not in the compose ``command:``, so that a
# plain ``docker run`` (or a cloud scheduler) also arrives at a migrated schema.
ENTRYPOINT ["/app/docker-entrypoint.sh"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
