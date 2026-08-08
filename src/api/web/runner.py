"""Background run orchestration — the same interpret -> plan -> execute ->
synth pipeline `api.cli.cmd_run` drives for the CLI, adapted to run behind
`POST /runs`'s immediate response (phase doc: "work happens in the
executor") and to persist the masterplan §4.10 SSE events around it.

The one real behavioural difference from the CLI path: disambiguation.
`api.cli` prints the chips and proceeds anyway (no caller could ever answer
them there). Here a plan-changing low-confidence field genuinely pauses the
run (`status='needs_input'`) until `PATCH /runs/{id}` supplies the resolved
brief — the "ordinary HTTP round trip" the phase doc's Design section
describes, deliberately not an in-graph interrupt mechanism.

Reuses `api.cli.build_deps`/`plan_to_execution_plan`/`run_coverage` rather
than re-deriving ~150 lines of client/dependency wiring the CLI already
built and tested in Phase 10.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta

import asyncpg
import httpx
import structlog

from api.cli import build_deps, plan_to_execution_plan, run_coverage
from api.config import Settings
from api.evidence.contradictions import resolve_contradictions
from api.executor.core import Executor
from api.llm.embed import build_embed_context
from api.models.brief import ResearchBrief
from api.models.events import FindingAddedEvent, PlanCreatedEvent, ReportReadyEvent
from api.planner.interpret import InterpretResult, interpret
from api.planner.plan import plan_stage1
from api.planner.registry import DEFAULT_MAX_COMPETITORS_PROFILED, DEFAULT_RUN_BUDGET_WEIGHT
from api.synth.assemble import RunMeta, assemble_report
from api.tasks.registry import build_registry
from api.web import sse
from api.web.quota import ConcurrencyQueue

logger = structlog.get_logger(__name__)


async def _pause_for_disambiguation(
    pool: asyncpg.Pool, run_id: str, interpretation: InterpretResult
) -> None:
    await pool.execute(
        """
        UPDATE runs
        SET status = 'needs_input', brief = $2::jsonb, keywords = $3::jsonb,
            disambiguation_fields = $4::jsonb
        WHERE id = $1
        """,
        run_id,
        interpretation.brief.model_dump_json(),
        json.dumps(interpretation.keywords),
        json.dumps(interpretation.disambiguation_fields),
    )


async def _fail_run(pool: asyncpg.Pool, run_id: str, error: str) -> None:
    await pool.execute(
        "UPDATE runs SET status = 'failed', finished_at = now() WHERE id = $1", run_id
    )
    logger.error("run_pipeline_failed", run_id=run_id, error=error)


async def run_pipeline(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    run_id: str,
    query: str,
    concurrency_queue: ConcurrencyQueue,
    resume_brief: ResearchBrief | None = None,
    resume_keywords: list[str] | None = None,
) -> None:
    """The `POST /runs` / `PATCH /runs/{id}` background task. Always
    releases its concurrency slot, and always leaves `runs.status` in a
    terminal-or-paused state (`needs_input`, `done`, or `failed`) — never
    left `running` forever on an unhandled error.
    """
    await concurrency_queue.acquire(run_id)
    start = time.monotonic()
    try:
        await pool.execute("UPDATE runs SET status = 'running' WHERE id = $1", run_id)
        deps = await build_deps(pool, http, settings, run_id=run_id, force_fetch=False)
        llm_ctx = deps.llm_context(None)

        if resume_brief is not None:
            brief, keywords = resume_brief, resume_keywords or []
        else:
            interpretation = await interpret(query, ctx=llm_ctx)
            if interpretation.disambiguation_fields:
                await _pause_for_disambiguation(pool, run_id, interpretation)
                return
            brief, keywords = interpretation.brief, interpretation.keywords
            await pool.execute(
                "UPDATE runs SET brief = $2::jsonb, keywords = $3::jsonb WHERE id = $1",
                run_id,
                brief.model_dump_json(),
                json.dumps(keywords),
            )

        run_budget_weight = settings.run_budget_weight or DEFAULT_RUN_BUDGET_WEIGHT
        max_competitors = settings.max_competitors_profiled or DEFAULT_MAX_COMPETITORS_PROFILED
        plan_outcome = await plan_stage1(
            brief,
            keywords,
            ctx=llm_ctx,
            run_budget_weight=run_budget_weight,
            max_competitors_profiled=max_competitors,
        )
        await sse.persist_event(
            pool, run_id, PlanCreatedEvent(run_id=run_id, plan=plan_outcome.plan)
        )

        execution_plan = plan_to_execution_plan(plan_outcome.plan)
        registry = build_registry(deps)
        executor = Executor(
            pool,
            registry,
            concurrency={
                "search": settings.search_concurrency,
                "crawl": settings.crawl_concurrency,
                "llm": settings.llm_concurrency,
            },
            lease_duration=timedelta(seconds=settings.task_lease_duration_s),
        )
        async for _event in executor.submit(
            run_id,
            execution_plan,
            budget_weight=run_budget_weight,
            budget_usd=settings.run_budget_usd,
        ):
            pass  # Executor.submit persists its own task.* events as it drives the run.

        await resolve_contradictions(pool, run_id)
        coverage = await run_coverage(pool, run_id, deps.stats)
        llm_cost = float(
            await pool.fetchval(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = $1", run_id
            )
        )
        search_cost = float(
            await pool.fetchval(
                "SELECT COALESCE(SUM(credits_usd), 0) FROM search_credit_usage WHERE run_id = $1",
                run_id,
            )
        )
        duration_s = time.monotonic() - start
        cache_hit_rate = (
            deps.stats.fetch_cache_hits / deps.stats.fetches_attempted
            if deps.stats.fetches_attempted
            else 0.0
        )

        embed_ctx = build_embed_context(
            pool=pool,
            http_client=http,
            api_key=settings.openrouter_api_key.get_secret_value(),
            run_id=run_id,
        )
        await assemble_report(
            pool,
            run_id=run_id,
            query=query,
            brief=brief,
            llm_ctx=llm_ctx,
            embed_ctx=embed_ctx,
            coverage=coverage,
            meta=RunMeta(
                cost_usd=llm_cost + search_cost,
                duration_s=duration_s,
                sources_fetched=deps.stats.fetches_attempted,
                cache_hit_rate=cache_hit_rate,
            ),
        )

        finding_rows = await pool.fetch(
            "SELECT id, kind, statement FROM findings WHERE run_id = $1 ORDER BY id", run_id
        )
        for row in finding_rows:
            await sse.persist_event(
                pool,
                run_id,
                FindingAddedEvent(
                    run_id=run_id,
                    finding_id=row["id"],
                    kind=row["kind"],
                    statement=row["statement"],
                ),
            )

        await pool.execute(
            "UPDATE runs SET status = 'done', cost_usd = $2, coverage = $3, finished_at = now() "
            "WHERE id = $1",
            run_id,
            llm_cost + search_cost,
            coverage.score,
        )
        await sse.persist_event(pool, run_id, ReportReadyEvent(run_id=run_id))
    except Exception as exc:  # noqa: BLE001 - a run's own failure must never crash the server
        await _fail_run(pool, run_id, str(exc))
    finally:
        await concurrency_queue.release(run_id)


__all__ = ["run_pipeline"]
