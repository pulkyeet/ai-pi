"""Embedding cache (Phase 11, `api.llm.embed`) — the one table backing this
codebase's only pgvector usage (masterplan §11: "pgvector only for complaint
near duplicate detection"; `CREATE EXTENSION vector` already ran in
migration `0001` anticipating this). Content-hash + model keyed, permanent
(no TTL) — mirrors `llm_response_cache`'s "deterministic function of its
key" reasoning: a fixed embedding model's output for a fixed input doesn't
change, so a cached row is never stale, only unreachable once the key
changes.

`EMBEDDING_DIM = 1536` matches `openai/text-embedding-3-small`
(`api.llm.embed`) — a real vendor-specific constant, not a guess.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE embedding_cache (
            cache_key  text PRIMARY KEY,
            model      text NOT NULL,
            embedding  vector(1536) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedding_cache")
