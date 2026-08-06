"""Raw SQL: task/event persistence. No ORM, per project convention.

Kept deliberately separate from `lease.py` — this module owns the
"write a plan into the tasks table" and "append/replay the event log"
concerns; `lease.py` owns the claim/complete/fail state machine.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from pydantic import BaseModel

from api.executor.protocol import EVENT_TYPES, ExecutionPlan, ExecutorEvent, SpawnRequest


async def insert_tasks(conn: asyncpg.Connection, run_id: str, plan: ExecutionPlan) -> None:
    if not plan.tasks:
        return
    await conn.executemany(
        """
        INSERT INTO tasks (run_id, node_key, kind, args, depends_on, budget_weight, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        ON CONFLICT (run_id, node_key) DO NOTHING
        """,
        [
            (
                run_id,
                t.node_key,
                t.kind,
                json.dumps(t.args),
                t.depends_on,
                t.budget_weight,
                t.priority,
            )
            for t in plan.tasks
        ],
    )


async def insert_spawned(
    conn: asyncpg.Connection, run_id: str, spawned: list[SpawnRequest]
) -> None:
    if not spawned:
        return
    await conn.executemany(
        """
        INSERT INTO tasks (run_id, node_key, kind, args, depends_on, budget_weight, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        ON CONFLICT (run_id, node_key) DO NOTHING
        """,
        [
            (
                run_id,
                s.node_key,
                s.kind,
                json.dumps(s.args),
                s.depends_on,
                s.budget_weight,
                s.priority,
            )
            for s in spawned
        ],
    )


async def persist_event(conn: asyncpg.Connection, run_id: str, event: ExecutorEvent) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO run_events (run_id, event_type, payload)
        VALUES ($1, $2, $3::jsonb)
        RETURNING id
        """,
        run_id,
        event.type,
        event.model_dump_json(),
    )
    assert row is not None
    return int(row["id"])


async def read_events(
    conn: asyncpg.Connection, run_id: str, since_id: int = 0
) -> list[tuple[int, ExecutorEvent]]:
    rows = await conn.fetch(
        "SELECT id, event_type, payload FROM run_events WHERE run_id = $1 AND id > $2 ORDER BY id",
        run_id,
        since_id,
    )
    return [(row["id"], _parse_event(row["event_type"], row["payload"])) for row in rows]


def _parse_event(event_type: str, payload: str | dict[str, Any]) -> ExecutorEvent:
    cls = EVENT_TYPES[event_type]
    data = json.loads(payload) if isinstance(payload, str) else payload
    model: BaseModel = cls.model_validate(data)
    return model  # type: ignore[return-value]
