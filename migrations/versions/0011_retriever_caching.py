"""Persistent retriever caching (Phase 14 follow-up, 2026-08-10).

Two tables filling the zero-spend replay gap (`docs/tuning.md` §6):

  1. `retriever_cache` — 24h TTL cache of domain-retriever search results
     (HN Algolia, GitHub Search). Masterplan §9 specifies three cache types
     (source 7d, search 24h, extraction permanent); domain retrievers fell
     outside all three, so a `--cached-only` replay still made live calls.
     Mirrors `search_cache`'s discipline: only successful responses are
     stored, a failure is never memoised. Keyed by sha256(provider + params).
  2. `robots_cache` — the parsed `robots.txt` body per host, 24h TTL.
     `RobotsCache` was in-memory only, so a fresh process (a new bench.runner
     invocation or CI job) re-fetched every domain's robots.txt even though
     the underlying page content was already Postgres-cached.

Both are positive-cache-only and expire — no cleanup job needed, matching
`search_cache`'s precedent.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-10

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"

depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE retriever_cache (
            id         bigserial PRIMARY KEY,
            cache_key  text UNIQUE NOT NULL,
            provider   text NOT NULL,
            payload    jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ON retriever_cache (expires_at)")

    op.execute(
        """
        CREATE TABLE robots_cache (
            host       text PRIMARY KEY,
            body       text NOT NULL,
            checked_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS robots_cache")
    op.execute("DROP TABLE IF EXISTS retriever_cache")
