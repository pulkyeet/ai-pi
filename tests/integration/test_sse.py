"""Phase 12 SSE tests (phase doc's testing table): ordered delivery,
lossless/duplicate-free reconnect via `Last-Event-ID`, terminal close on
`report.ready` or run failure, heartbeats, and access control on the stream
endpoint itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import asyncpg
import httpx
import pytest
from _auth import new_user_id, sign_jwt
from _webapp import build_http_client, build_settings, seed_auth_user
from sse_starlette.sse import EventSourceResponse

from api.models.events import (
    PlanCreatedEvent,
    ReportReadyEvent,
    TaskCompletedEvent,
    TaskStartedEvent,
)
from api.models.plan import Plan
from api.web import killswitch, sse
from api.web.app import create_app

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


@pytest.fixture(autouse=True)
async def _reset_kill_switch(pg_pool: asyncpg.Pool) -> None:
    await killswitch.reset(pg_pool)


def _asgi_client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _seed_run(
    pool: asyncpg.Pool, *, user_id: str, status: str = "running", is_public: bool = False
) -> str:
    run_id = f"r_test_{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO runs (id, user_id, query, status, is_public, started_at) "
        "VALUES ($1, $2, 'test query', $3, $4, now())",
        run_id,
        uuid.UUID(user_id),
        status,
        is_public,
    )
    return run_id


async def _collect_until_terminal(
    pool: asyncpg.Pool, run_id: str, *, since_id: int = 0, timeout_s: float = 2.0
) -> list[dict[str, str]]:
    """`sse.stream_events` yields `sse_starlette`-shaped frames (`{"id":
    ..., "event": ..., "data": ...}`), not `(id, RunEvent)` pairs — that
    unpacking is `read_new_public_events`'s shape, one layer down."""
    collected: list[dict[str, str]] = []

    async def _run() -> None:
        async for frame in sse.stream_events(pool, run_id, since_id=since_id):
            collected.append(frame)

    await asyncio.wait_for(_run(), timeout=timeout_s)
    return collected


# ---------------------------------------------------------------------------
# Ordering + terminal close
# ---------------------------------------------------------------------------


async def test_events_delivered_in_order_and_stream_closes_on_report_ready(
    pg_pool: asyncpg.Pool,
) -> None:
    owner = new_user_id()
    await seed_auth_user(pg_pool, owner)
    run_id = await _seed_run(pg_pool, user_id=owner)

    events = [
        PlanCreatedEvent(run_id=run_id, plan=Plan(nodes=[], edges=[], total_budget_weight=0)),
        TaskStartedEvent(run_id=run_id, task_id=1, kind="discover_competitors"),
        TaskCompletedEvent(run_id=run_id, task_id=1, kind="discover_competitors", cost_usd=0.01),
        ReportReadyEvent(run_id=run_id),
    ]
    for event in events:
        await sse.persist_event(pg_pool, run_id, event)
    await pg_pool.execute("UPDATE runs SET status = 'done' WHERE id = $1", run_id)

    collected = await _collect_until_terminal(pg_pool, run_id)
    assert [frame["event"] for frame in collected] == [
        "plan.created",
        "task.started",
        "task.completed",
        "report.ready",
    ]


async def test_stream_closes_on_terminal_failure_with_no_report_ready(
    pg_pool: asyncpg.Pool,
) -> None:
    owner = new_user_id()
    await seed_auth_user(pg_pool, owner)
    run_id = await _seed_run(pg_pool, user_id=owner)

    await sse.persist_event(
        pg_pool, run_id, TaskStartedEvent(run_id=run_id, task_id=1, kind="discover_competitors")
    )
    await pg_pool.execute("UPDATE runs SET status = 'failed' WHERE id = $1", run_id)

    collected = await _collect_until_terminal(pg_pool, run_id)
    assert [frame["event"] for frame in collected] == ["task.started"]


# ---------------------------------------------------------------------------
# Reconnect: lossless, no duplication
# ---------------------------------------------------------------------------


async def test_reconnect_with_last_event_id_resumes_without_loss_or_duplication(
    pg_pool: asyncpg.Pool,
) -> None:
    owner = new_user_id()
    await seed_auth_user(pg_pool, owner)
    run_id = await _seed_run(pg_pool, user_id=owner)

    ids = [
        await sse.persist_event(
            pg_pool,
            run_id,
            TaskStartedEvent(run_id=run_id, task_id=i, kind="discover_competitors"),
        )
        for i in range(3)
    ]

    # A client that only ever saw the first event reconnects with
    # `Last-Event-ID` set to it.
    resumed = await sse.read_new_public_events(pg_pool, run_id, ids[0])
    resumed_ids = [event_id for event_id, _event in resumed]
    assert resumed_ids == ids[1:]

    # And a client reconnecting from the very last id it saw gets nothing
    # new yet — no duplicates fabricated out of thin air.
    empty = await sse.read_new_public_events(pg_pool, run_id, ids[-1])
    assert empty == []

    # A fresh (non-reconnecting) read from 0 gets everything, once each.
    everything = await sse.read_new_public_events(pg_pool, run_id, 0)
    assert [event_id for event_id, _event in everything] == ids


async def test_internal_only_events_are_not_surfaced_on_the_public_stream(
    pg_pool: asyncpg.Pool,
) -> None:
    """`task.skipped`/`task.progress`/`run.finished` are real
    `api.executor.protocol.ExecutorEvent` types persisted into the same
    `run_events` table by `Executor.submit` itself, but are not part of the
    masterplan §4.10 public vocabulary — `read_new_public_events` must skip
    them, not error on them.
    """
    owner = new_user_id()
    await seed_auth_user(pg_pool, owner)
    run_id = await _seed_run(pg_pool, user_id=owner)

    await pg_pool.execute(
        "INSERT INTO run_events (run_id, event_type, payload) "
        "VALUES ($1, 'task.skipped', $2::jsonb)",
        run_id,
        f'{{"type": "task.skipped", "run_id": "{run_id}", "task_id": 1, "node_key": "n", '
        '"kind": "discover_competitors", "reason": "budget"}',
    )
    await sse.persist_event(
        pg_pool, run_id, TaskStartedEvent(run_id=run_id, task_id=2, kind="discover_competitors")
    )

    events = await sse.read_new_public_events(pg_pool, run_id, 0)
    assert [event.type for _id, event in events] == ["task.started"]


# ---------------------------------------------------------------------------
# Access control on the stream endpoint
# ---------------------------------------------------------------------------


async def test_events_endpoint_enforces_the_same_access_control(pg_pool: asyncpg.Pool) -> None:
    http, _transport = build_http_client()
    app = create_app(build_settings(), pg_pool, http)
    owner, other = new_user_id(), new_user_id()
    await seed_auth_user(pg_pool, owner)
    await seed_auth_user(pg_pool, other)
    run_id = await _seed_run(pg_pool, user_id=owner, is_public=False)
    other_token = sign_jwt(sub=other)

    try:
        async with _asgi_client(app) as client:
            resp = await client.get(
                f"/runs/{run_id}/events", headers={"Authorization": f"Bearer {other_token}"}
            )
        assert resp.status_code == 404
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------


async def test_heartbeat_frames_keep_a_quiet_stream_alive(pg_pool: asyncpg.Pool) -> None:
    """Exercises the real `sse_starlette` ping mechanism (a short interval,
    not the app's real 15s, so this stays fast) against a run with no
    events at all — proving a quiet stream still emits comment frames
    rather than going silent. Drives the ASGI callable directly rather
    than through `httpx.ASGITransport`, which buffers a response's full
    body before returning it and so cannot observe an in-progress,
    never-ending stream.
    """
    owner = new_user_id()
    await seed_auth_user(pg_pool, owner)
    run_id = await _seed_run(pg_pool, user_id=owner)

    response = EventSourceResponse(sse.stream_events(pg_pool, run_id, since_id=0), ping=0.05)
    sent: list[bytes] = []

    async def receive() -> dict[str, object]:
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        body = message.get("body")
        if message["type"] == "http.response.body" and isinstance(body, bytes):
            sent.append(body)

    scope = {"type": "http", "method": "GET", "path": "/events", "headers": []}
    task = asyncio.create_task(response(scope, receive, send))
    try:
        await asyncio.sleep(0.2)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert any(b"ping" in chunk for chunk in sent)
