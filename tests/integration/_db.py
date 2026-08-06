"""Shared Postgres test helpers for executor integration/chaos tests."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg


async def insert_run(conn: asyncpg.Connection) -> str:
    unique = uuid.uuid4().hex
    user_id = await conn.fetchval(
        "INSERT INTO auth.users (email) VALUES ($1) RETURNING id", f"{unique}@example.com"
    )
    run_id = f"r_test_{unique}"
    await conn.execute(
        "INSERT INTO runs (id, user_id, query) VALUES ($1, $2, 'test query')", run_id, user_id
    )
    return run_id


async def insert_task(
    conn: asyncpg.Connection,
    run_id: str,
    node_key: str,
    *,
    kind: str = "noop",
    args: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
    priority: int = 0,
    budget_weight: int = 1,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO tasks (run_id, node_key, kind, args, depends_on, budget_weight, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        RETURNING id
        """,
        run_id,
        node_key,
        kind,
        json.dumps(args or {}),
        depends_on or [],
        budget_weight,
        priority,
    )
    assert row is not None
    return int(row["id"])


async def task_status(conn: asyncpg.Connection, task_id: int) -> str:
    value = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", task_id)
    return str(value)


async def ensure_side_effects_table(conn: asyncpg.Connection) -> None:
    """Scratch table for synthetic handlers proving no-double-execution and
    the generic (domain-free) analog of the claims table's Guard 2 —
    UNIQUE + ON CONFLICT DO NOTHING absorbing a forced duplicate write.
    Not a migration: this is test harness plumbing, not product schema.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chaos_side_effects (
            run_id   text NOT NULL,
            node_key text NOT NULL,
            UNIQUE (run_id, node_key)
        )
        """
    )
