"""Run-level extraction metrics (Phase 15: metrics endpoint).

One row per completed run, written at run-finish time by the same code that
sets `runs.status` to `done` (`api.cli.run_query` and
`api.web.runner.run_pipeline`), feeding the authenticated `GET /metrics`
endpoint's "extraction drop rate" and the runbook's binding/drop alerts
(docs/execution_phases/phase-15-deployment-observability.md).

Why a new table rather than reading `runs` alone: `api.tasks.context.
RunStats.claims_bound`/`claims_dropped` are in-memory counters — never
persisted — so the drop data a metrics endpoint needs is otherwise
unknowable after the fact (the benchmark's own drop breakdown lives in
`bench/results/*.json` snapshot files, not the database).

Backward-compatible by construction: an old image never writes this table
and never reads it, so a rollback to the previous release leaves no
stranded schema state.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-11

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run_stats (
            run_id          text PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
            claims_bound    int NOT NULL,
            claims_dropped  jsonb NOT NULL DEFAULT '{}',
            recorded_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_stats")
