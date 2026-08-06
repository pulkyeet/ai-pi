"""Fetch, text extraction & source cache (Phase 03).

Additions over 0002 (see docs/execution_phases/phase-03-fetch-source-cache.md):

  1. `sources.etag` / `sources.last_modified` — conditional-request state, so
     a refetch can send `If-None-Match` / `If-Modified-Since` and a 304
     refreshes the TTL without re-transferring or re-extracting.
  2. `path_guess_cache` — one row per (registrable domain, path kind)
     recording the resolved candidate path (or NULL if none matched), so a
     domain with no `/pricing` is not re-probed every run. Positive and
     negative rows share the table; `expires_at` differs (7d vs 24h) per the
     phase doc's asymmetric TTL rule — sites add pricing pages, so a miss is
     re-checked sooner than a hit is invalidated.
  3. `path_guess_attempts` — append-only instrumentation log, one row per
     candidate path actually requested. This is the raw material for the
     measured path-guessing hit rate reported in `docs/external_apis.md`.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-06

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sources ADD COLUMN etag text")
    op.execute("ALTER TABLE sources ADD COLUMN last_modified text")

    op.execute(
        """
        CREATE TABLE path_guess_cache (
            root_key   text NOT NULL,
            path_kind  text NOT NULL,
            found_path text,
            checked_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            PRIMARY KEY (root_key, path_kind)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE path_guess_attempts (
            id         bigserial PRIMARY KEY,
            root_key   text NOT NULL,
            path_kind  text NOT NULL,
            path       text NOT NULL,
            outcome    text NOT NULL CHECK (outcome IN ('hit', 'miss')),
            checked_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON path_guess_attempts (root_key, checked_at)")
    op.execute("CREATE INDEX ON path_guess_cache (expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS path_guess_attempts")
    op.execute("DROP TABLE IF EXISTS path_guess_cache")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS last_modified")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS etag")
