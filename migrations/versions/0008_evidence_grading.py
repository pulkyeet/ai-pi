"""Evidence grading, confidence & contradictions (Phase 08).

One additive column: `claims.confidence_inputs`. The confidence formula
(masterplan §4.6) is deterministic and tunable, but only if its inputs
survive alongside the result — otherwise a Phase 14 recalibration of the
formula constants would need to re-run extraction just to recompute
existing claims' confidence. Stores `api.evidence.confidence.ConfidenceInputs`
as JSON (`best_grade`, `n_distinct_domains`, `age_days`, `contradicted`).

Grading (`api.evidence.grade`) and contradiction detection
(`api.evidence.contradictions`) need no schema changes: grading is a pure
function computed before a `Claim` is ever constructed, and contradiction
detection/resolution operates entirely on columns `0001_initial` already
defined (`grade`, `value_text`, `value_num`, `as_of`, `superseded_by`).

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE claims ADD COLUMN confidence_inputs jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS confidence_inputs")
