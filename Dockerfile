# AI Product Investigator — one image, two entrypoints (Phase 15).
#
# The API machine (`fly.toml`) runs `python -m api.web.main`; the worker
# machine (`fly.worker.toml`) runs `python -m api.worker`. Both share this
# image so there is exactly one thing to build and one thing to version
# (phase doc: "Two machines, one image, one managed database").
#
# Base images are pinned by SHA-256 digest (resolved 2026-08-11) so a rebuild
# is reproducible; the only moving part is the tag each digest points at.
#
# Local verify: `docker build -t ai-pi:local .`

# --- Stage 1: build the venv with uv, frozen against uv.lock ---------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder

WORKDIR /app

# Install the pinned dependencies first (uv caches this layer) — no source
# yet, so the project itself is skipped until it exists.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then bring in the runtime and the migration set (alembic needs both to run
# in-image migrations if ever required) and install the project.
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# --- Stage 2: minimal runtime image -----------------------------------------
FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

# Non-root user, per the phase doc ("multi-stage, non-root user, pinned base
# image digest").
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

USER app

# The API machine listens on 8000 (api.web.main). The worker machine exposes
# no port; Fly's HTTP health check applies to the API machine only.
EXPOSE 8000

# Default entrypoint = the API. The worker machine overrides this with
# `fly machine run ... python -m api.worker` / `fly machine update --command`.
CMD ["python", "-m", "api.web.main"]
