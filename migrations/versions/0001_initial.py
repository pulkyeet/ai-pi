"""Initial schema: masterplan §4.3 with the Phase 00 deltas.

Deltas over the raw masterplan table
(see docs/execution_phases/phase-00-foundation-contracts-ci.md):
  1. `user_profiles` keyed to Supabase's `auth.users`, no duplicate `users`/`identities`.
     A minimal `auth.users` stub is created locally only if the `auth` schema doesn't
     already exist, so this migration is a no-op against real Supabase.
  2. `claims.quote_context` / `context_offset` so drill-down survives `sources` TTL eviction.
  3. `sources.is_pinned` exempts benchmark sources from TTL eviction.

Revision ID: 0001
Revises:
Create Date: 2026-08-06

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Local-only auth.users stub so the FK below resolves without Supabase.
    # Skipped entirely when the `auth` schema already exists (real Supabase project).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth'
            ) THEN
                CREATE SCHEMA auth;
                CREATE TABLE auth.users (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    email text UNIQUE
                );
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE user_profiles (
            user_id         uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
            quota_override  int,
            is_admin        bool NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE runs (
            id           text PRIMARY KEY,
            user_id      uuid NOT NULL REFERENCES auth.users(id),
            query        text NOT NULL,
            brief        jsonb,
            status       text NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'running', 'done', 'failed')),
            cost_usd     numeric,
            coverage     numeric,
            is_benchmark bool NOT NULL DEFAULT false,
            is_public    bool NOT NULL DEFAULT false,
            started_at   timestamptz,
            finished_at  timestamptz
        )
        """
    )

    op.execute(
        """
        CREATE TABLE entities (
            id           bigserial PRIMARY KEY,
            entity_key   text UNIQUE NOT NULL,
            display_name text NOT NULL,
            maturity     text
                         CHECK (maturity IN
                             ('established', 'funded', 'indie', 'hobby', 'abandoned')),
            meta         jsonb NOT NULL DEFAULT '{}'
        )
        """
    )

    op.execute(
        """
        CREATE TABLE entity_aliases (
            id        bigserial PRIMARY KEY,
            entity_id bigint NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            alias_key text UNIQUE NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE sources (
            id               bigserial PRIMARY KEY,
            canonical_url    text UNIQUE NOT NULL,
            root_key         text,
            fetched_at       timestamptz,
            http_status      int,
            content_hash     text,
            extracted_text   text,
            retrieval_reason text,
            ttl_expires_at   timestamptz,
            is_pinned        bool NOT NULL DEFAULT false
        )
        """
    )

    op.execute(
        """
        CREATE TABLE tasks (
            id               bigserial PRIMARY KEY,
            run_id           text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            kind             text NOT NULL,
            args             jsonb NOT NULL DEFAULT '{}',
            status           text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'running', 'done', 'failed', 'skipped')),
            priority         int NOT NULL DEFAULT 0,
            attempts         int NOT NULL DEFAULT 0,
            lease_token      uuid,
            lease_expires_at timestamptz,
            cost_usd         numeric,
            latency_ms       int,
            error            text
        )
        """
    )

    op.execute(
        """
        CREATE TABLE claims (
            id               bigserial PRIMARY KEY,
            run_id           text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            entity_id        bigint NOT NULL REFERENCES entities(id),
            attribute        text NOT NULL,
            value_text       text,
            value_num        numeric,
            unit             text,
            as_of            date,
            source_id        bigint NOT NULL REFERENCES sources(id),
            quote            text NOT NULL,
            char_start       int NOT NULL,
            char_end         int NOT NULL,
            quote_context    text NOT NULL,
            context_offset   int NOT NULL,
            grade            text NOT NULL CHECK (grade IN ('A', 'B', 'C', 'D')),
            extractor_version text NOT NULL,
            confidence       numeric NOT NULL,
            superseded_by    bigint REFERENCES claims(id),
            CONSTRAINT claims_span_valid CHECK (char_end > char_start AND char_start >= 0),
            CONSTRAINT claims_unique_span UNIQUE (run_id, source_id, attribute, char_start)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE findings (
            id            bigserial PRIMARY KEY,
            run_id        text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            kind          text NOT NULL,
            statement     text NOT NULL,
            claim_ids     bigint[] NOT NULL,
            support_count int,
            confidence    numeric,
            CONSTRAINT findings_must_cite CHECK (cardinality(claim_ids) >= 1)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE reports (
            id      bigserial PRIMARY KEY,
            run_id  text UNIQUE NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            payload jsonb NOT NULL
        )
        """
    )

    op.execute("CREATE INDEX ON tasks (run_id, status, priority)")
    op.execute("CREATE INDEX ON tasks (status, lease_expires_at) WHERE status = 'running'")
    op.execute("CREATE INDEX ON claims (run_id, entity_id, attribute) WHERE superseded_by IS NULL")
    op.execute("CREATE INDEX ON sources (ttl_expires_at) WHERE is_pinned = false")
    op.execute("CREATE INDEX ON entity_aliases (alias_key)")


def downgrade() -> None:
    # The auth.users stub (if this migration created it) is intentionally left in
    # place on downgrade: dropping it is unsafe to automate against a real Supabase
    # project, and re-running upgrade() is idempotent either way.
    op.execute("DROP TABLE IF EXISTS reports")
    op.execute("DROP TABLE IF EXISTS findings")
    op.execute("DROP TABLE IF EXISTS claims")
    op.execute("DROP TABLE IF EXISTS tasks")
    op.execute("DROP TABLE IF EXISTS sources")
    op.execute("DROP TABLE IF EXISTS entity_aliases")
    op.execute("DROP TABLE IF EXISTS entities")
    op.execute("DROP TABLE IF EXISTS runs")
    op.execute("DROP TABLE IF EXISTS user_profiles")
