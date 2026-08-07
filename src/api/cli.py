"""`python -m api.cli` — Phase 10's concrete output: a CLI that runs a real
product-idea query end to end (interpret -> plan -> execute -> claims in
Postgres) and streams the same event types Phase 13's frontend will later
render as a live checklist (phase doc: "getting it working in a terminal
first means the stream is proven before any UI depends on it").

```
python -m api.cli run "AI expense tracker for freelancers"
python -m api.cli run "..." --budget 20 --no-cache
python -m api.cli inspect <run_id>
python -m api.cli replay <run_id>
```

This module owns everything the seven task handlers (`api.tasks`) don't:
constructing every real client (Postgres pool, HTTP client, every Phase 04
retriever, the search router), the `runs` row's own lifecycle (Phase 00's
table has no other owner yet — Phase 12 is what eventually puts an
authenticated HTTP API in front of it), adapting a planner `Plan` into the
executor's domain-agnostic `ExecutionPlan` at the boundary (`api.executor`
deliberately knows nothing about `api.models.plan` — see that package's own
module docstring), driving `Executor.submit`, and printing the phase doc's
own instrumentation table from what `api.tasks.context.RunStats` collected
plus the `llm_calls`/`search_credit_usage` ledgers.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from datetime import timedelta
from typing import cast

import asyncpg
import httpx

from api.config import Settings
from api.db import create_pool
from api.evidence.contradictions import resolve_contradictions
from api.evidence.coverage import CoverageResult, TaskOutcome, TaskStatus, compute_coverage
from api.executor.core import Executor
from api.executor.protocol import (
    ExecutionPlan,
    ExecutorEvent,
    RunFinished,
    TaskCompleted,
    TaskFailed,
    TaskSkipped,
    TaskSpec,
    TaskStarted,
)
from api.llm.gateway import LLMContext
from api.models.plan import TASK_COST_WEIGHT, Plan, TaskKind
from api.planner.interpret import interpret
from api.planner.plan import plan_stage1
from api.planner.registry import DEFAULT_MAX_COMPETITORS_PROFILED, DEFAULT_RUN_BUDGET_WEIGHT
from api.retrieval import HostThrottle, RobotsCache, build_client
from api.search.budget import RetrievalBudget
from api.search.exa import ExaProvider
from api.search.router import SearchRouter
from api.sources.github import GitHubRetriever
from api.sources.hn import HNRetriever
from api.sources.packages import PackagesRetriever
from api.sources.producthunt import ProductHuntRetriever
from api.sources.reddit import RedditRetriever
from api.sources.stackexchange import StackExchangeRetriever
from api.sources.wayback import WaybackRetriever
from api.tasks.context import (
    DEFAULT_MAX_COMMUNITY_THREADS,
    DEFAULT_MAX_FETCHES_PER_RUN,
    DEFAULT_MAX_PAGES_PER_ENTITY,
    DEFAULT_MAX_SEARCHES_PER_RUN,
    HandlerDeps,
    RunStats,
)
from api.tasks.registry import build_registry

CLI_USER_EMAIL = "cli@ai-pi.local"


async def ensure_cli_user(pool: asyncpg.Pool) -> uuid.UUID:
    """No authenticated caller exists yet (Phase 12) — the CLI runs as one
    fixed local user, upserted once and reused across invocations."""
    row = await pool.fetchrow(
        """
        INSERT INTO auth.users (email) VALUES ($1)
        ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
        RETURNING id
        """,
        CLI_USER_EMAIL,
    )
    assert row is not None
    return cast(uuid.UUID, row["id"])


async def create_run(pool: asyncpg.Pool, query: str) -> str:
    user_id = await ensure_cli_user(pool)
    run_id = f"r_{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO runs (id, user_id, query, status, started_at) "
        "VALUES ($1, $2, $3, 'running', now())",
        run_id,
        user_id,
        query,
    )
    return run_id


async def finish_run(pool: asyncpg.Pool, run_id: str, *, cost_usd: float, coverage: float) -> None:
    await pool.execute(
        "UPDATE runs SET status = 'done', cost_usd = $2, coverage = $3, finished_at = now() "
        "WHERE id = $1",
        run_id,
        cost_usd,
        coverage,
    )


def plan_to_execution_plan(plan: Plan) -> ExecutionPlan:
    """`api.executor` deliberately has zero imports from `api.models.plan` —
    this is the one place a real `Plan` becomes the executor's generic
    `ExecutionPlan`. Masterplan `edges` are `(a, b)` meaning "b depends on
    a"; `TaskSpec.depends_on` is the inverse shape (each task names its own
    dependencies), so this inverts the edge list once.

    **A real contract mismatch between Phase 09 and Phase 02, found here.**
    Phase 09's `discover_competitors` node stores an *inflated*
    `budget_weight` — its own registry cost plus room for the
    `profile_product`/`extract_pricing` children it hasn't spawned yet (see
    `api.planner.fallback`'s and `plan_dag.md`'s own comments: "budget for
    the anticipated fan-out is reserved on that same node's
    `budget_weight`... rather than invented as phantom nodes"). But
    `api.executor.budget.BudgetTracker` deducts a task's *own*
    `TaskSpec.budget_weight` from the run cap the moment that task is
    admitted — it has no concept of "this weight is a bucket my children
    will draw down from later". Passing the inflated value straight through
    double-counts: `discover_competitors` alone would consume the entire
    run budget, leaving nothing for the children it spawns, and every one
    of them would be skipped for "budget" despite the plan itself being
    exactly the size the planner intended. Fixed here, not in
    `api.planner` or `api.executor`: a `discover_competitors` `TaskSpec`
    charges only its own registry cost (`TASK_COST_WEIGHT[DISCOVER_
    COMPETITORS]`); `Plan.total_budget_weight` (the inflated figure) is
    still what's passed as the *run's* overall cap to `Executor.submit`, so
    the headroom the planner reserved is exactly what's left over for the
    real spawned children to draw against. Every other seed kind's stored
    `budget_weight` already equals its own registry cost (nothing else
    spawns further children in v1), so this adjustment is a no-op for them.
    """
    depends_on: dict[str, list[str]] = {n.id: [] for n in plan.nodes}
    for a, b in plan.edges:
        depends_on[b].append(a)
    return ExecutionPlan(
        tasks=[
            TaskSpec(
                node_key=node.id,
                kind=node.kind.value,
                args=node.args,
                budget_weight=(
                    TASK_COST_WEIGHT[TaskKind.DISCOVER_COMPETITORS]
                    if node.kind is TaskKind.DISCOVER_COMPETITORS
                    else node.budget_weight
                ),
                depends_on=depends_on[node.id],
            )
            for node in plan.nodes
        ]
    )


async def build_deps(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    run_id: str,
    force_fetch: bool,
) -> HandlerDeps:
    throttle = HostThrottle()
    robots = RobotsCache(http)

    producthunt_token = (
        settings.producthunt_token.get_secret_value() if settings.producthunt_token else None
    )
    reddit_client_id = (
        settings.reddit_client_id.get_secret_value() if settings.reddit_client_id else None
    )
    reddit_client_secret = (
        settings.reddit_client_secret.get_secret_value() if settings.reddit_client_secret else None
    )
    langfuse_secret_key = (
        settings.langfuse_secret_key.get_secret_value() if settings.langfuse_secret_key else None
    )

    exa = ExaProvider(http, settings.exa_api_key.get_secret_value())
    search_router = SearchRouter(
        pool, exa, run_id=run_id, daily_credit_cap_usd=settings.exa_daily_credit_cap_usd
    )

    return HandlerDeps(
        pool=pool,
        http=http,
        throttle=throttle,
        robots=robots,
        github=GitHubRetriever(http, settings.github_token.get_secret_value()),
        hn=HNRetriever(http),
        stackexchange=StackExchangeRetriever(http),
        wayback=WaybackRetriever(http),
        packages=PackagesRetriever(http),
        producthunt=ProductHuntRetriever(http, producthunt_token),
        reddit=RedditRetriever(
            http,
            enabled=settings.enable_reddit,
            client_id=reddit_client_id,
            client_secret=reddit_client_secret,
        ),
        search_router=search_router,
        retrieval_budget=RetrievalBudget(
            max_searches=DEFAULT_MAX_SEARCHES_PER_RUN, max_fetches=DEFAULT_MAX_FETCHES_PER_RUN
        ),
        run_id=run_id,
        llm_api_key=settings.openrouter_api_key.get_secret_value(),
        llm_model=settings.llm_model,
        langfuse_public_key=settings.langfuse_public_key,
        langfuse_secret_key=langfuse_secret_key,
        langfuse_host=settings.langfuse_host,
        max_pages_per_entity=settings.max_pages_per_entity or DEFAULT_MAX_PAGES_PER_ENTITY,
        max_community_threads=settings.max_community_threads or DEFAULT_MAX_COMMUNITY_THREADS,
        force_fetch=force_fetch,
        stats=RunStats(),
    )


def print_event(event: ExecutorEvent) -> None:
    if isinstance(event, TaskStarted):
        print(f"  -> {event.node_key} ({event.kind}) started")
    elif isinstance(event, TaskCompleted):
        print(f"  OK {event.node_key} done in {event.latency_ms}ms (${event.cost_usd:.4f})")
    elif isinstance(event, TaskFailed):
        print(f"  XX {event.node_key} failed after {event.attempts} attempts: {event.error}")
    elif isinstance(event, TaskSkipped):
        print(f"  -- {event.node_key} skipped ({event.reason})")
    elif isinstance(event, RunFinished):
        print(
            f"run.finished: done={event.done} failed={event.failed} "
            f"skipped={event.skipped} coverage={event.coverage:.2f}"
        )


async def run_coverage(pool: asyncpg.Pool, run_id: str, stats: RunStats) -> CoverageResult:
    """`api.evidence.coverage.compute_coverage` (Phase 08), fed from this
    run's real task outcomes — `tasks.error` doubles as the skip reason on a
    `skipped` row (`api.executor.lease.skip_claimed`/`skip_unreachable`),
    which is exactly the shape `TaskOutcome.skip_reason` expects."""
    rows = await pool.fetch(
        "SELECT kind, budget_weight, status, error FROM tasks WHERE run_id = $1", run_id
    )
    outcomes = [
        TaskOutcome(
            kind=row["kind"],
            cost_weight=row["budget_weight"],
            status=cast(TaskStatus, row["status"]),
            skip_reason=row["error"] if row["status"] == "skipped" else None,
        )
        for row in rows
    ]
    return compute_coverage(
        outcomes,
        total_entities=stats.entities_verified,
        insufficient_signal_entities=stats.insufficient_signal_entities,
    )


def print_summary(
    stats: RunStats,
    *,
    duration_s: float,
    llm_cost: float,
    search_cost: float,
    coverage: CoverageResult,
) -> None:
    print()
    print("=== run summary ===")
    print(f"duration:          {duration_s:.1f}s")
    print(
        f"cost:              ${llm_cost + search_cost:.4f} "
        f"(llm ${llm_cost:.4f}, search ${search_cost:.4f})"
    )
    print(
        f"searches:          {stats.searches_attempted} attempted, {stats.search_degraded} degraded"
    )
    print(
        f"fetches:           {stats.fetches_attempted} attempted, "
        f"{stats.fetch_cache_hits} cache hits"
    )
    print(f"claims:            {stats.claims_bound} bound, dropped={dict(stats.claims_dropped)}")
    print(
        f"entities:          {stats.entities_verified} verified, "
        f"{stats.entities_rejected} rejected, "
        f"{stats.insufficient_signal_entities} insufficient-signal"
    )
    print(
        f"coverage:          {coverage.score:.2f} "
        f"(failed={list(coverage.failed_branches)}, "
        f"budget_skipped={list(coverage.budget_skipped_branches)}, "
        f"other_skipped={list(coverage.other_skipped_branches)})"
    )


async def cmd_run(query: str, *, budget: int | None, no_cache: bool) -> None:
    settings = Settings()  # type: ignore[call-arg]
    pool = await create_pool(settings)
    http = build_client()
    start = time.monotonic()
    try:
        run_id = await create_run(pool, query)
        print(f"run_id: {run_id}")

        deps = await build_deps(pool, http, settings, run_id=run_id, force_fetch=no_cache)
        llm_ctx: LLMContext = deps.llm_context(None)

        interpretation = await interpret(query, ctx=llm_ctx)
        print(f"brief: {interpretation.brief.model_dump()}")
        if interpretation.disambiguation_fields:
            print(
                "(disambiguation fields, proceeding with the best guess: "
                f"{interpretation.disambiguation_fields})"
            )
        await pool.execute(
            "UPDATE runs SET brief = $2::jsonb WHERE id = $1",
            run_id,
            interpretation.brief.model_dump_json(),
        )

        run_budget_weight = budget or settings.run_budget_weight or DEFAULT_RUN_BUDGET_WEIGHT
        max_competitors = settings.max_competitors_profiled or DEFAULT_MAX_COMPETITORS_PROFILED
        plan_outcome = await plan_stage1(
            interpretation.brief,
            interpretation.keywords,
            ctx=llm_ctx,
            run_budget_weight=run_budget_weight,
            max_competitors_profiled=max_competitors,
        )
        fallback_note = " (fallback)" if plan_outcome.used_fallback else ""
        print(
            f"plan: {len(plan_outcome.plan.nodes)} seed node(s), "
            f"budget {plan_outcome.plan.total_budget_weight}{fallback_note}"
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

        print("executing:")
        async for event in executor.submit(
            run_id,
            execution_plan,
            budget_weight=run_budget_weight,
            budget_usd=settings.run_budget_usd,
        ):
            print_event(event)

        resolutions = await resolve_contradictions(pool, run_id)
        if resolutions:
            print(f"contradictions resolved: {len(resolutions)}")

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
        await finish_run(pool, run_id, cost_usd=llm_cost + search_cost, coverage=coverage.score)

        print_summary(
            deps.stats,
            duration_s=time.monotonic() - start,
            llm_cost=llm_cost,
            search_cost=search_cost,
            coverage=coverage,
        )
    finally:
        await http.aclose()
        await pool.close()


async def cmd_inspect(run_id: str) -> None:
    """The debugging surface the masterplan promises: "Debugging is a SQL
    query against a task table" (§12.1) — the DAG with per-task status,
    timing, cost, error; claims grouped by entity and attribute."""
    settings = Settings()  # type: ignore[call-arg]
    pool = await create_pool(settings)
    try:
        run = await pool.fetchrow(
            "SELECT id, query, status, cost_usd, coverage, started_at, finished_at "
            "FROM runs WHERE id = $1",
            run_id,
        )
        if run is None:
            print(f"no such run: {run_id}")
            return
        print(
            f"run {run['id']}: {run['query']!r} status={run['status']} "
            f"cost=${run['cost_usd']} coverage={run['coverage']}"
        )

        tasks = await pool.fetch(
            "SELECT node_key, kind, status, attempts, cost_usd, latency_ms, error "
            "FROM tasks WHERE run_id = $1 ORDER BY id",
            run_id,
        )
        print("\ntasks:")
        for t in tasks:
            print(
                f"  {t['node_key']:<28} {t['kind']:<22} {t['status']:<9} "
                f"attempts={t['attempts']} cost=${t['cost_usd'] or 0} "
                f"latency={t['latency_ms'] or 0}ms {t['error'] or ''}"
            )

        claims = await pool.fetch(
            "SELECT e.display_name, c.attribute, c.value_text, c.value_num, c.grade, c.confidence "
            "FROM claims c JOIN entities e ON e.id = c.entity_id "
            "WHERE c.run_id = $1 AND c.superseded_by IS NULL "
            "ORDER BY e.display_name, c.attribute",
            run_id,
        )
        print("\nclaims by entity:")
        current: str | None = None
        for c in claims:
            if c["display_name"] != current:
                current = c["display_name"]
                print(f"  {current}:")
            value = c["value_text"] if c["value_text"] is not None else c["value_num"]
            print(
                f"    {c['attribute']} = {value!r}  grade={c['grade']} confidence={c['confidence']}"
            )
    finally:
        await pool.close()


async def cmd_replay(run_id: str) -> None:
    """Re-run `run_id`'s query from scratch, as a *new* run — free and
    deterministic because every layer's own cache (source: 7d, search: 24h,
    LLM response + extraction: permanent) turns a repeat call into a hit
    rather than a re-fetch. No separate replay machinery needed."""
    settings = Settings()  # type: ignore[call-arg]
    pool = await create_pool(settings)
    try:
        row = await pool.fetchrow("SELECT query FROM runs WHERE id = $1", run_id)
        if row is None:
            print(f"no such run: {run_id}")
            return
        query = str(row["query"])
    finally:
        await pool.close()
    print(f"replaying {run_id}'s query ({query!r}) as a new run, from caches:")
    await cmd_run(query, budget=None, no_cache=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m api.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a query end to end")
    run_p.add_argument("query")
    run_p.add_argument("--budget", type=int, default=None, help="override run_budget_weight")
    run_p.add_argument("--no-cache", action="store_true", help="force-refetch pages (best effort)")

    inspect_p = sub.add_parser("inspect", help="inspect a run's tasks, claims, and cost")
    inspect_p.add_argument("run_id")

    replay_p = sub.add_parser("replay", help="re-run a run's query from caches, zero spend")
    replay_p.add_argument("run_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        asyncio.run(cmd_run(args.query, budget=args.budget, no_cache=args.no_cache))
    elif args.command == "inspect":
        asyncio.run(cmd_inspect(args.run_id))
    elif args.command == "replay":
        asyncio.run(cmd_replay(args.run_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
