"""Entity resolution verification cache (Phase 07).

`entities` / `entity_aliases` already exist from `0001_initial` (Phase 00
froze their shape). The one new table this phase needs is
`verification_cache`: masterplan Rule 2 (§2) requires every entity to have
a verified public artifact, and re-verifying the same candidate every run
would waste the Phase 04 search/fetch budget for `web:` candidates and burn
free-tier request quota for the registry/store/API lookups behind every
other scheme. 24h TTL, mirroring `search_cache`'s reasoning (Phase 04) — a
failed verification is cached too, so a dead candidate isn't re-probed
every run either.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE verification_cache (
            entity_key      text PRIMARY KEY,
            verified        bool NOT NULL,
            grade           text NOT NULL CHECK (grade IN ('A', 'B', 'C', 'D')),
            reason          text,
            homepage_url    text,
            repository_url  text,
            checked_at      timestamptz NOT NULL,
            expires_at      timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ON verification_cache (expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verification_cache")
