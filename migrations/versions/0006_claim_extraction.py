"""Claim extraction cache (Phase 06).

`extraction_cache` — permanent, keyed `(content_hash, extractor_version)`
(masterplan §9): the same page under the same extractor version costs
nothing forever. Both key components are content-addressed (a changed page
changes `content_hash`; a changed prompt or model changes
`extractor_version`), so a stale entry is unreachable rather than wrong —
no TTL column, matching `llm_response_cache`'s reasoning in Phase 05.

Stores the model's raw claims, pre vocabulary/value-type/span validation —
see `src/api/extract/cache.py`'s module docstring for why re-validating on
every read, not just on write, matters.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE extraction_cache (
            content_hash      text NOT NULL,
            extractor_version text NOT NULL,
            claims            jsonb NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (content_hash, extractor_version)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS extraction_cache")
