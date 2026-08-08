"""API, Auth, Quotas & Guardrails (Phase 12).

Additions:
  1. `system_state` — a singleton row (`id = 1`, enforced by CHECK) holding the
     kill switch. A database flag, not a deploy (phase doc's Design section):
     flipped automatically when the global daily cap trips, and readable by
     `GET /health` without any other table.
  2. `runs.status` gains `needs_input` — the paused state between `POST /runs`
     returning disambiguation chips and the `PATCH /runs/{id}` that resolves
     them (phase doc: "the run waits for a PATCH with the resolved brief").
  3. `runs.keywords` / `runs.disambiguation_fields` — Stage 0's `keywords`
     (needed to resume `plan_stage1` after a pause, but deliberately not part
     of `ResearchBrief` itself, see `api.planner.interpret`'s own module
     docstring) and the chip fields shown to the caller while paused.
  4. Indexes on `runs (user_id, started_at)` / `runs (started_at)` — the
     per-user and global quota windows are both `count(*) ... started_at >
     now() - interval '1 day'` queries, run on every `POST /runs`.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runs DROP CONSTRAINT runs_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE runs ADD CONSTRAINT runs_status_check
        CHECK (status IN ('pending', 'needs_input', 'running', 'done', 'failed'))
        """
    )
    op.execute("ALTER TABLE runs ADD COLUMN keywords jsonb")
    op.execute("ALTER TABLE runs ADD COLUMN disambiguation_fields jsonb")

    op.execute("CREATE INDEX runs_user_id_started_at_idx ON runs (user_id, started_at)")
    op.execute("CREATE INDEX runs_started_at_idx ON runs (started_at)")

    op.execute(
        """
        CREATE TABLE system_state (
            id                   int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            kill_switch_enabled  bool NOT NULL DEFAULT false,
            kill_switch_reason   text,
            updated_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("INSERT INTO system_state (id) VALUES (1)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS system_state")
    op.execute("DROP INDEX IF EXISTS runs_started_at_idx")
    op.execute("DROP INDEX IF EXISTS runs_user_id_started_at_idx")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS disambiguation_fields")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS keywords")
    op.execute("ALTER TABLE runs DROP CONSTRAINT runs_status_check")
    op.execute(
        """
        ALTER TABLE runs ADD CONSTRAINT runs_status_check
        CHECK (status IN ('pending', 'running', 'done', 'failed'))
        """
    )
