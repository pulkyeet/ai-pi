"""Search & domain retrievers (Phase 04).

Two new tables (see docs/execution_phases/phase-04-search-domain-retrievers.md):

  1. `search_cache` — 24h TTL cache of search results, keyed by a stable hash
     of (query, provider, params). Shared across users/runs: a second query in
     an already-explored category is nearly free. Mirrors the shape of Phase
     03's `path_guess_cache`/`sources` caches (positive rows only; a provider
     failure is never cached, it degrades instead — see `api.search.router`).
  2. `search_credit_usage` — append-only ledger, one row per real (non-cached)
     provider call, in dollars ("credits, not calls" — masterplan's search
     cost is a monthly allowance, not a per-query bill). This is what the
     daily/global-daily allowance checks in `api.search.router` sum over;
     both stay unenforced (`settings.exa_daily_credit_cap_usd is None`) until
     Phase 14 measures real credits-per-run.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-06

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE search_cache (
            id           bigserial PRIMARY KEY,
            cache_key    text UNIQUE NOT NULL,
            provider     text NOT NULL,
            query        text NOT NULL,
            params       jsonb NOT NULL DEFAULT '{}',
            results      jsonb NOT NULL,
            credits_usd  numeric NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            expires_at   timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ON search_cache (expires_at)")

    op.execute(
        """
        CREATE TABLE search_credit_usage (
            id          bigserial PRIMARY KEY,
            provider    text NOT NULL,
            run_id      text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            credits_usd numeric NOT NULL,
            spent_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON search_credit_usage (spent_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search_credit_usage")
    op.execute("DROP TABLE IF EXISTS search_cache")
