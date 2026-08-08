"""SSE event stream (phase doc's "SSE" design) — persistence and replay for
the six masterplan §4.10 event types: `plan.created`, `task.started`,
`task.completed`, `task.failed`, `finding.added`, `report.ready`.

Deliberately a *different* vocabulary from `api.executor.protocol.
ExecutorEvent` (which also has `task.progress`, `task.skipped`,
`run.finished` — internal executor telemetry, not the public contract).
Both live in the same `run_events` table (Phase 02's append-only log,
`id` a global monotonic cursor) — `api.executor.core.Executor` persists its
own events automatically as it drives a run; `api.web.runner` persists the
three wrapper events (`plan.created`, `finding.added`, `report.ready`) at
the right points around it. `read_new_public_events` is what tells the two
vocabularies apart on read: an `event_type` outside `EVENT_TYPES` here is
skipped, not an error — it is real internal telemetry a future admin view
could still read directly from `run_events`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

import asyncpg
from pydantic import BaseModel

from api.models.events import (
    FindingAddedEvent,
    PlanCreatedEvent,
    ReportReadyEvent,
    RunEvent,
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskStartedEvent,
)

EVENT_TYPES: dict[str, type[BaseModel]] = {
    "plan.created": PlanCreatedEvent,
    "task.started": TaskStartedEvent,
    "task.completed": TaskCompletedEvent,
    "task.failed": TaskFailedEvent,
    "finding.added": FindingAddedEvent,
    "report.ready": ReportReadyEvent,
}

POLL_INTERVAL_S = 0.5


async def persist_event(pool: asyncpg.Pool, run_id: str, event: RunEvent) -> int:
    row = await pool.fetchrow(
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


def _parse(event_type: str, payload: str | dict[str, Any]) -> RunEvent | None:
    cls = EVENT_TYPES.get(event_type)
    if cls is None:
        return None
    data = json.loads(payload) if isinstance(payload, str) else payload
    return cast(RunEvent, cls.model_validate(data))


async def read_new_public_events(
    pool: asyncpg.Pool, run_id: str, since_id: int
) -> list[tuple[int, RunEvent]]:
    rows = await pool.fetch(
        "SELECT id, event_type, payload FROM run_events WHERE run_id = $1 AND id > $2 ORDER BY id",
        run_id,
        since_id,
    )
    out: list[tuple[int, RunEvent]] = []
    for row in rows:
        event = _parse(row["event_type"], row["payload"])
        if event is not None:
            out.append((int(row["id"]), event))
    return out


async def stream_events(
    pool: asyncpg.Pool, run_id: str, *, since_id: int
) -> AsyncIterator[dict[str, str]]:
    """Yields `sse_starlette`-shaped event dicts in order, replaying
    everything after `since_id` (a reconnect's `Last-Event-ID`), then
    polling for new ones. Closes after `report.ready`, or once the run's own
    `status` turns `failed` — the phase doc's two terminal-close conditions.
    Heartbeats are the caller's job (`EventSourceResponse(..., ping=...)`),
    not this generator's.
    """
    cursor = since_id
    while True:
        events = await read_new_public_events(pool, run_id, cursor)
        for event_id, event in events:
            cursor = event_id
            yield {"id": str(event_id), "event": event.type, "data": event.model_dump_json()}
            if isinstance(event, ReportReadyEvent):
                return
        status = await pool.fetchval("SELECT status FROM runs WHERE id = $1", run_id)
        if status == "failed":
            return
        await asyncio.sleep(POLL_INTERVAL_S)


__all__ = ["EVENT_TYPES", "persist_event", "read_new_public_events", "stream_events"]
