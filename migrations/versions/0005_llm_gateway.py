"""LLM gateway (Phase 05).

Two new tables (see docs/execution_phases/phase-05-llm-gateway.md):

  1. `llm_calls` — append-only cost/telemetry ledger, one row per
     `api.llm.gateway.structured()` call (cache hits included, at
     `cost_usd = 0`, so `cache_hit_rate`/`repair_rate` are real SQL
     aggregates over this table — mirrors `search_credit_usage`'s "dollars,
     not counts" shape from Phase 04, plus the two metrics the phase doc
     calls out as needing to be tracked, not just eyeballed: cache hit rate
     and repair rate).
  2. `llm_response_cache` — the transport-level response cache keyed on
     `hash(prompt_version + model + rendered_messages)`. Permanent (no TTL
     column): a hit is a deterministic function of its key at temperature 0,
     so a cached row is never stale, only unreachable once the key changes
     (edited prompt -> new prompt_version; different variables -> different
     rendered messages). Distinct from Phase 06's future extraction cache
     (`content_hash + extractor_version`, a different cache one layer up).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE llm_calls (
            id             bigserial PRIMARY KEY,
            run_id         text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            task_id        bigint REFERENCES tasks(id) ON DELETE SET NULL,
            prompt_id      text NOT NULL,
            prompt_version text NOT NULL,
            model          text NOT NULL,
            provider       text NOT NULL,
            input_tokens   int NOT NULL,
            output_tokens  int NOT NULL,
            cached_tokens  int NOT NULL,
            cost_usd       numeric NOT NULL,
            latency_ms     int NOT NULL,
            cache_hit      bool NOT NULL DEFAULT false,
            repaired       bool NOT NULL DEFAULT false,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON llm_calls (run_id)")
    op.execute("CREATE INDEX ON llm_calls (task_id)")

    op.execute(
        """
        CREATE TABLE llm_response_cache (
            cache_key      text PRIMARY KEY,
            prompt_id      text NOT NULL,
            prompt_version text NOT NULL,
            model          text NOT NULL,
            content        text NOT NULL,
            input_tokens   int NOT NULL,
            output_tokens  int NOT NULL,
            cached_tokens  int NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON llm_response_cache (prompt_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_response_cache")
    op.execute("DROP TABLE IF EXISTS llm_calls")
