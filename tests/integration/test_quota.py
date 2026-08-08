"""Phase 12 quota/concurrency tests — the phase doc's own emphasis: "the
quota atomicity test is the one worth writing carefully. A naive
check-then-insert passes every single-threaded test and fails exactly when
it matters."
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import httpx
import pytest
from _auth import new_user_id, sign_jwt
from _webapp import build_http_client, build_settings, seed_auth_user

from api.web import killswitch
from api.web.app import create_app
from api.web.errors import QuotaExceededError
from api.web.quota import ConcurrencyQueue, try_create_run

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


@pytest.fixture(autouse=True)
async def _reset_kill_switch(pg_pool: asyncpg.Pool) -> None:
    # `system_state` is a shared singleton row in the long-lived
    # `ai_pi_test` database (docs/working_knowledge.md's recurring
    # persistent-table lesson) — reset before every test in this module so
    # none of them depend on whatever a previous run (or another test file)
    # left behind.
    await killswitch.reset(pg_pool)


def _asgi_client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _noop_run_pipeline(*args: object, **kwargs: object) -> None:
    return None


# ---------------------------------------------------------------------------
# Atomicity: N concurrent requests at limit N-1 admit exactly N-1
# ---------------------------------------------------------------------------


async def test_quota_atomicity_admits_exactly_n_minus_one(pg_pool: asyncpg.Pool) -> None:
    user_id = uuid.UUID(new_user_id())
    await seed_auth_user(pg_pool, str(user_id))
    n = 8
    quota = n - 1

    async def _one() -> None:
        await try_create_run(
            pg_pool,
            run_id=f"r_test_{uuid.uuid4().hex}",
            user_id=user_id,
            query="a note app",
            per_user_quota=quota,
            global_quota=None,
        )

    results = await asyncio.gather(*[_one() for _ in range(n)], return_exceptions=True)
    successes = [r for r in results if r is None]
    failures = [r for r in results if isinstance(r, QuotaExceededError)]
    assert len(successes) == quota
    assert len(failures) == n - quota
    for other in results:
        if other is not None and not isinstance(other, QuotaExceededError):
            raise other

    stored = await pg_pool.fetchval("SELECT count(*) FROM runs WHERE user_id = $1", user_id)
    assert stored == quota


async def test_quota_unenforced_when_none(pg_pool: asyncpg.Pool) -> None:
    user_id = uuid.UUID(new_user_id())
    await seed_auth_user(pg_pool, str(user_id))
    for _ in range(5):
        await try_create_run(
            pg_pool,
            run_id=f"r_test_{uuid.uuid4().hex}",
            user_id=user_id,
            query="a note app",
            per_user_quota=None,
            global_quota=None,
        )
    stored = await pg_pool.fetchval("SELECT count(*) FROM runs WHERE user_id = $1", user_id)
    assert stored == 5


# ---------------------------------------------------------------------------
# quota_override respected
# ---------------------------------------------------------------------------


async def test_quota_override_respected(
    pg_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.web.routes.runs.run_pipeline", _noop_run_pipeline)
    http, _transport = build_http_client()
    app = create_app(build_settings(runs_per_user_per_day=1), pg_pool, http)
    user_id = new_user_id()
    await seed_auth_user(pg_pool, user_id)
    await pg_pool.execute(
        "INSERT INTO user_profiles (user_id, quota_override) VALUES ($1, 3)", uuid.UUID(user_id)
    )
    token = sign_jwt(sub=user_id)

    try:
        async with _asgi_client(app) as client:
            for _ in range(3):
                resp = await client.post(
                    "/runs",
                    json={"query": "a note app for freelancers"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 202
            blocked = await client.post(
                "/runs",
                json={"query": "another one entirely"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "user_quota_exceeded"
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# Global cap trips the kill switch; reports stay served
# ---------------------------------------------------------------------------


async def test_global_cap_trips_kill_switch_reports_still_served(
    pg_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.web.routes.runs.run_pipeline", _noop_run_pipeline)
    http, _transport = build_http_client()
    # `runs` is a shared, persistent table across this whole test session
    # (docs/working_knowledge.md's own recurring lesson) — the global cap
    # counts *every* run from the last 24h, so the cap must be set relative
    # to whatever is already there, not a bare literal.
    already_today = await pg_pool.fetchval(
        "SELECT count(*) FROM runs WHERE started_at > now() - interval '1 day'"
    )
    app = create_app(build_settings(global_runs_per_day=already_today + 1), pg_pool, http)
    user_a, user_b = new_user_id(), new_user_id()
    await seed_auth_user(pg_pool, user_a)
    await seed_auth_user(pg_pool, user_b)
    token_a, token_b = sign_jwt(sub=user_a), sign_jwt(sub=user_b)

    try:
        async with _asgi_client(app) as client:
            first = await client.post(
                "/runs",
                json={"query": "a note app"},
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert first.status_code == 202

            second = await client.post(
                "/runs",
                json={"query": "a different app idea"},
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert second.status_code == 503
            assert second.json()["error"]["code"] == "live_runs_paused"
            assert "tomorrow" in second.json()["error"]["message"]

            health = await client.get("/health")
            assert health.json()["kill_switch_enabled"] is True

            benchmark = await client.get("/reports/benchmark")
            assert benchmark.status_code == 200
    finally:
        # `system_state` is a shared singleton across the whole test
        # session (the same "persistent table" trap as `runs`'s global
        # count above) — tripping it here must not leak into every other
        # test that hits `POST /runs` afterward.
        await killswitch.reset(pg_pool)
        await http.aclose()


# ---------------------------------------------------------------------------
# Concurrency: queues rather than rejects; position reported and decreases
# ---------------------------------------------------------------------------


async def test_concurrency_queue_reports_decreasing_position() -> None:
    queue = ConcurrencyQueue(max_concurrent=1)
    release_a = asyncio.Event()

    async def worker(run_id: str, release: asyncio.Event) -> None:
        await queue.acquire(run_id)
        await release.wait()
        await queue.release(run_id)

    task_a = asyncio.create_task(worker("a", release_a))
    await asyncio.sleep(0.02)
    assert queue.position("a") == 0

    release_b = asyncio.Event()
    task_b = asyncio.create_task(worker("b", release_b))
    await asyncio.sleep(0.02)
    assert queue.position("b") == 1

    release_a.set()
    await task_a
    await asyncio.sleep(0.02)
    assert queue.position("b") == 0

    release_b.set()
    await task_b


async def test_concurrency_queue_unenforced_when_none() -> None:
    queue = ConcurrencyQueue(max_concurrent=None)
    await queue.acquire("a")
    await queue.acquire("b")
    assert queue.position("a") == 0
    assert queue.position("b") == 0
    await queue.release("a")
    await queue.release("b")


async def test_get_run_reports_queue_position(
    pg_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_event = asyncio.Event()

    async def _blocking_run_pipeline(
        pool: asyncpg.Pool,
        http: httpx.AsyncClient,
        settings: object,
        *,
        run_id: str,
        query: str,
        concurrency_queue: ConcurrencyQueue,
        resume_brief: object | None = None,
        resume_keywords: object | None = None,
    ) -> None:
        await concurrency_queue.acquire(run_id)
        await release_event.wait()
        await concurrency_queue.release(run_id)

    monkeypatch.setattr("api.web.routes.runs.run_pipeline", _blocking_run_pipeline)
    http, _transport = build_http_client()
    app = create_app(build_settings(max_concurrent_runs=1), pg_pool, http)
    user_id = new_user_id()
    await seed_auth_user(pg_pool, user_id)
    token = sign_jwt(sub=user_id)

    try:
        async with _asgi_client(app) as client:
            first = await client.post(
                "/runs", json={"query": "a note app"}, headers={"Authorization": f"Bearer {token}"}
            )
            run_id_1 = first.json()["run_id"]
            second = await client.post(
                "/runs",
                json={"query": "a second note app idea"},
                headers={"Authorization": f"Bearer {token}"},
            )
            run_id_2 = second.json()["run_id"]
            await asyncio.sleep(0.02)

            auth_header = {"Authorization": f"Bearer {token}"}
            status_1 = await client.get(f"/runs/{run_id_1}", headers=auth_header)
            status_2 = await client.get(f"/runs/{run_id_2}", headers=auth_header)
            assert status_1.json()["queue_position"] == 0
            assert status_2.json()["queue_position"] == 1
    finally:
        release_event.set()
        await asyncio.sleep(0.02)
        await http.aclose()
