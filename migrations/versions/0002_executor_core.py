"""Executor core (Phase 02): task dependency graph, budget weight, event log.

Additions over 0001, needed by the domain-agnostic executor (see
docs/execution_phases/phase-02-executor-core.md):

  1. `tasks.node_key` — the executor's own DAG-node identifier (distinct from
     `id`), so dependency edges and dynamically spawned children can be
     addressed before/without knowing the Postgres-assigned id. Unique per run.
  2. `tasks.depends_on` — sibling `node_key`s this task waits on. A task is
     claimable only once every entry is `done`; if a dependency ends `failed`
     or `skipped` the task can never become claimable and is swept up as a
     dead branch when the run drains (see `executor.lease.skip_unreachable`).
  3. `tasks.budget_weight` — persisted per task (not just held in the
     in-memory `Plan`) so a crash-recovered task still carries its weight.
  4. `run_events` — persisted event log backing `Executor.submit`'s
     async-iterator stream, so a reconnecting/replaying consumer can resume
     from a cursor (`id` is a global monotonic sequence; reads always filter
     by `run_id`, so global monotonicity is sufficient without a per-run
     counter).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-06

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks ADD COLUMN node_key text")
    op.execute("ALTER TABLE tasks ADD COLUMN depends_on text[] NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE tasks ADD COLUMN budget_weight int NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT tasks_node_key_unique UNIQUE (run_id, node_key)")

    op.execute(
        """
        CREATE TABLE run_events (
            id         bigserial PRIMARY KEY,
            run_id     text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            event_type text NOT NULL,
            payload    jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON run_events (run_id, id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_events")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_node_key_unique")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS budget_weight")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS depends_on")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS node_key")
