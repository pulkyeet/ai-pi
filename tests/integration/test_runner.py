"""`api.web.runner.run_pipeline` tests: the disambiguation pause and the
happy path through to `report.ready`. `interpret`/`plan_stage1`/
`assemble_report` are the three points that would otherwise need real
OpenRouter/Exa traffic (Phase 01/05's own fixture-corpus convention keeps
that out of this tier); `build_deps`, the real `Executor` (driven with an
intentionally empty `Plan`, so it drains instantly with zero task
handlers), `resolve_contradictions`, and `run_coverage` all run for real —
every one of them is pure construction or a plain Postgres query with no
external I/O.
"""

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
import pytest
from _auth import new_user_id
from _webapp import build_http_client, build_settings, seed_auth_user

from api.models.brief import ResearchBrief
from api.models.plan import Plan
from api.models.report import MVP, Coverage, Freshness, Meta, PricingLandscape, Report
from api.planner.interpret import InterpretResult
from api.planner.plan import PlanOutcome
from api.web import sse
from api.web.quota import ConcurrencyQueue
from api.web.runner import run_pipeline

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


async def _seed_pending_run(pool: asyncpg.Pool, *, user_id: str, query: str) -> str:
    run_id = f"r_test_{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO runs (id, user_id, query, status, started_at) "
        "VALUES ($1, $2, $3, 'pending', now())",
        run_id,
        uuid.UUID(user_id),
        query,
    )
    return run_id


async def test_run_pipeline_pauses_for_disambiguation(
    pg_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_interpret(query: str, *, ctx: object) -> InterpretResult:
        return InterpretResult(
            brief=ResearchBrief(category="x", segment="y", geography="z", monetisation_guess="w"),
            keywords=["note", "app"],
            disambiguation_fields=["category"],
        )

    monkeypatch.setattr("api.web.runner.interpret", fake_interpret)

    owner = new_user_id()
    await seed_auth_user(pg_pool, owner)
    run_id = await _seed_pending_run(pg_pool, user_id=owner, query="a note app")
    http, _transport = build_http_client()
    queue = ConcurrencyQueue(None)

    settings = build_settings()
    try:
        await run_pipeline(
            pg_pool,
            http,
            settings,
            run_id=run_id,
            query="a note app",
            concurrency_queue=queue,
        )

        row = await pg_pool.fetchrow(
            "SELECT status, brief, keywords, disambiguation_fields FROM runs WHERE id = $1", run_id
        )
        assert row is not None
        assert row["status"] == "needs_input"
        assert row["disambiguation_fields"] == '["category"]'
        assert queue.position(run_id) == 0  # slot released, not left held
    finally:
        await http.aclose()
        # `needs_input` is a Phase 12 status value a pre-Phase-12 downgrade's
        # narrower CHECK constraint can't accept — unlike this project's
        # tolerated shared/persistent tables (caches, ledgers), a lingering
        # row here breaks `test_migrations.py`'s downgrade cycle for every
        # test that runs afterward, so this one cleans up after itself.
        await pg_pool.execute("DELETE FROM runs WHERE id = $1", run_id)


async def test_run_pipeline_happy_path_reaches_report_ready(
    pg_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_interpret(query: str, *, ctx: object) -> InterpretResult:
        return InterpretResult(
            brief=ResearchBrief(category="x", segment="y", geography="z", monetisation_guess="w"),
            keywords=["note", "app"],
            disambiguation_fields=[],
        )

    async def fake_plan_stage1(
        brief: ResearchBrief,
        keywords: list[str],
        *,
        ctx: object,
        run_budget_weight: int,
        max_competitors_profiled: int,
    ) -> PlanOutcome:
        return PlanOutcome(
            plan=Plan(nodes=[], edges=[], total_budget_weight=0),
            used_fallback=False,
            repaired=False,
        )

    async def fake_assemble_report(
        pool: asyncpg.Pool,
        *,
        run_id: str,
        query: str,
        brief: ResearchBrief,
        llm_ctx: object,
        embed_ctx: object,
        coverage: object,
        meta: object,
    ) -> Report:
        await pool.execute(
            "INSERT INTO findings (run_id, kind, statement, claim_ids) "
            "VALUES ($1, 'pain_point', 'a real pain point', ARRAY[1]::bigint[])",
            run_id,
        )
        report = Report(
            run_id=run_id,
            query=query,
            brief=brief,
            competitors=[],
            pricing_landscape=PricingLandscape(
                median_entry_usd_month=0, spread=(0, 0), claim_ids=[]
            ),
            pain_points=[],
            feature_gaps=[],
            contradictions=[],
            mvp=MVP(statement="", addresses_finding_ids=[]),
            risks=[],
            coverage=Coverage(score=0, failed_branches=[]),
            freshness=Freshness(median_source_age_days=0, oldest=date.today()),
            meta=Meta(cost_usd=0, duration_s=0, sources_fetched=0, cache_hit_rate=0),
        )
        await pool.execute(
            "INSERT INTO reports (run_id, payload) VALUES ($1, $2::jsonb)",
            run_id,
            report.model_dump_json(),
        )
        return report

    monkeypatch.setattr("api.web.runner.interpret", fake_interpret)
    monkeypatch.setattr("api.web.runner.plan_stage1", fake_plan_stage1)
    monkeypatch.setattr("api.web.runner.assemble_report", fake_assemble_report)

    owner = new_user_id()
    await seed_auth_user(pg_pool, owner)
    run_id = await _seed_pending_run(pg_pool, user_id=owner, query="a note app")
    http, _transport = build_http_client()
    queue = ConcurrencyQueue(None)

    settings = build_settings()
    try:
        await run_pipeline(
            pg_pool,
            http,
            settings,
            run_id=run_id,
            query="a note app",
            concurrency_queue=queue,
        )
    finally:
        await http.aclose()

    status = await pg_pool.fetchval("SELECT status FROM runs WHERE id = $1", run_id)
    assert status == "done"

    events = await sse.read_new_public_events(pg_pool, run_id, 0)
    assert [event.type for _id, event in events] == [
        "plan.created",
        "finding.added",
        "report.ready",
    ]

    report_row = await pg_pool.fetchval("SELECT payload FROM reports WHERE run_id = $1", run_id)
    assert report_row is not None
