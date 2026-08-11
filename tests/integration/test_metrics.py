"""Phase 15: `GET /metrics` — the authenticated operational-metrics endpoint
the runbook checks against the phase doc's nine-alert table."""

from __future__ import annotations

import json
import os
import uuid

import _webapp
import asyncpg
import httpx
import pytest
from _auth import new_user_id, sign_jwt
from _webapp import build_http_client, build_settings, seed_auth_user

from api.web.app import create_app

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


@pytest.fixture(autouse=True)
def _point_webapp_at_test_db() -> None:
    # `_webapp.build_settings` reads a module-level constant, not the env
    # conftest uses — keep both pointing at the same database.
    _webapp.TEST_DATABASE_URL = os.environ.get(
        "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ai_pi_test"
    )


async def _seed_metrics_data(pool: asyncpg.Pool) -> None:
    """A recent done run with cost, tasks, claims (one bound, one unbound),
    run_stats, and search spend — everything `_collect_metrics` reads."""
    unique = uuid.uuid4().hex
    user_id = await pool.fetchval(
        "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
        f"{unique}@example.com",
    )
    assert user_id is not None
    run_id = f"r_metrics_{unique}"
    await pool.execute(
        "INSERT INTO runs (id, user_id, query, status, started_at, finished_at, cost_usd) "
        "VALUES ($1, $2, 'metrics test', 'done', now() - interval '2 hours', "
        "now() - interval '1 hour', 0.10)",
        run_id,
        user_id,
    )
    source_id = await pool.fetchval(
        "INSERT INTO sources (canonical_url, extracted_text) "
        "VALUES ($1, 'the quick brown fox jumps') RETURNING id",
        f"https://metrics-{unique}.example.com/page",
    )
    entity_id = await pool.fetchval(
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, 'x') RETURNING id",
        f"web:metrics-{unique}.example.com",
    )
    assert source_id is not None
    assert entity_id is not None
    await pool.execute(
        """
        INSERT INTO claims (run_id, entity_id, source_id, attribute, quote, char_start,
                            char_end, quote_context, context_offset, grade,
                            extractor_version, confidence)
        VALUES
            ($1, $2, $3, 'product.launch_date', 'quick brown', 4, 15, '', 0, 'A', 'test', 0.5),
            ($1, $2, $3, 'pricing.entry_usd_month', 'never present on this page',
             0, 26, '', 0, 'A', 'test', 0.5)
        """,
        run_id,
        entity_id,
        source_id,
    )
    await pool.execute(
        "INSERT INTO run_stats (run_id, claims_bound, claims_dropped) VALUES ($1, 10, $2::jsonb)",
        run_id,
        json.dumps({"quote_not_in_source": 2}),
    )
    await pool.execute(
        "INSERT INTO tasks (run_id, node_key, kind, status) VALUES "
        "($1, 'a', 'noop', 'done'), ($1, 'b', 'noop', 'failed')",
        run_id,
    )
    await pool.execute(
        "INSERT INTO search_credit_usage (provider, run_id, credits_usd) VALUES ('exa', $1, 0.50)",
        run_id,
    )


async def _client(pool: asyncpg.Pool) -> httpx.AsyncClient:
    settings = build_settings()
    http, _transport = build_http_client()
    app = create_app(settings, pool, http)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_metrics_requires_auth(pg_pool: asyncpg.Pool) -> None:
    async with await _client(pg_pool) as client:
        response = await client.get("/metrics")
    assert response.status_code == 401


async def test_metrics_reports_values_and_thresholds(pg_pool: asyncpg.Pool) -> None:
    await _seed_metrics_data(pg_pool)
    user_id = new_user_id()
    await seed_auth_user(pg_pool, user_id)
    token = sign_jwt(sub=user_id)

    async with await _client(pg_pool) as client:
        response = await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["db_size_bytes"] > 0
    by_key = {m["key"]: m for m in body["metrics"]}
    assert set(by_key) == {
        "runs_today",
        "cost_per_run_mean",
        "search_spend_mtd",
        "sentence_binding_rate",
        "extraction_drop_rate",
        "p95_run_latency_s",
        "task_failure_rate",
        "db_size_pct",
    }

    # Exact thresholds are the "wired as documented" surface and are
    # deterministic constants; values are asserted as *bounds* because the
    # shared, long-lived `ai_pi_test` DB accumulates rows from every test
    # file in a session (the documented shared-table hazard), so a seeded
    # value can only be guaranteed to be *present in* each aggregate.
    assert by_key["runs_today"]["value"] >= 1.0  # the seeded run started today
    assert by_key["runs_today"]["threshold"] == 4.0  # DEFAULT_GLOBAL_RUNS_PER_DAY

    assert by_key["cost_per_run_mean"]["value"] > 0
    assert by_key["cost_per_run_mean"]["threshold"] == pytest.approx(2 * 0.0621)

    assert by_key["search_spend_mtd"]["value"] >= 0.50
    assert by_key["search_spend_mtd"]["threshold"] == pytest.approx(7.0)  # 70% of $10

    # One of the seeded recent claims deliberately does not bind, so the
    # live binding-rate check must read < 1.0 and the page-level alert fires.
    binding = by_key["sentence_binding_rate"]
    assert binding["value"] < 1.0
    assert binding["threshold"] == 1.0
    assert binding["alert_level"] == "page"
    assert binding["breached"] is True

    drop = by_key["extraction_drop_rate"]
    assert drop["value"] > 0  # the seeded 2 drops are inside the window
    assert drop["threshold"] == pytest.approx(1.5 * 0.20)

    latency = by_key["p95_run_latency_s"]
    assert latency["threshold"] == pytest.approx(640 * 0.8)  # default RUN_TIMEOUT_S

    assert by_key["task_failure_rate"]["value"] > 0  # the seeded failed task
    assert by_key["task_failure_rate"]["threshold"] == pytest.approx(2 * 0.05)

    assert by_key["db_size_pct"]["threshold"] == pytest.approx(0.70)


async def test_metrics_is_well_formed_on_any_database_state(pg_pool: asyncpg.Pool) -> None:
    user_id = new_user_id()
    await seed_auth_user(pg_pool, user_id)
    token = sign_jwt(sub=user_id)

    async with await _client(pg_pool) as client:
        response = await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["db_size_bytes"] > 0
    keys = {m["key"] for m in body["metrics"]}
    assert keys == {
        "runs_today",
        "cost_per_run_mean",
        "search_spend_mtd",
        "sentence_binding_rate",
        "extraction_drop_rate",
        "p95_run_latency_s",
        "task_failure_rate",
        "db_size_pct",
    }
    # A metric with no data in its window must read as not-breached — an
    # absent value can never be a false positive.
    for metric in body["metrics"]:
        if metric["value"] is None:
            assert metric["breached"] is False
