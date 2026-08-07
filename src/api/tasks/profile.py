"""`profile_product` — path-guess `/pricing`, `/docs`, `/changelog` for the
entity's own domain, fetch the homepage, extract claims from each (Phase 10
phase doc). Almost no searching, per masterplan §7 — this handler is where
the path-guessing cost saving from Phase 03 is actually realised.

The fetch/guess/extract/persist sequence (`fetch_and_extract`,
`resolve_entity_id`, `task_llm_cost`) is shared with `pricing.py`
(`extract_pricing` is explicitly "narrower and cheaper than
`profile_product`: pricing paths only" — the same handler shape at a smaller
page set) rather than duplicated.
"""

from __future__ import annotations

from typing import Any

import structlog

from api.evidence.grade import classify_own_domain_fetch, grade_for
from api.executor.protocol import HandlerResult, ServiceName, TaskContext
from api.extract.extractor import extract_claims
from api.llm.gateway import LLMValidationError
from api.models.entity import EntityKey, EntityScheme
from api.models.plan import TASK_COST_WEIGHT, TaskKind
from api.models.source import CacheOutcome
from api.retrieval.errors import FetchError
from api.retrieval.fetch import fetch_source
from api.retrieval.pathguess import guess_path
from api.search.budget import BudgetExhaustedError
from api.tasks.claims import persist_extracted_claims
from api.tasks.context import HandlerDeps

logger = structlog.get_logger()

# Homepage plus these three, in order, up to `HandlerDeps.max_pages_per_entity`.
PATH_KINDS_FULL: tuple[str, ...] = ("pricing", "docs", "changelog")


async def resolve_entity_id(deps: HandlerDeps, entity_key: str) -> tuple[int, EntityKey] | None:
    """`entities` has no unique in-memory registry — a spawned task only
    carries the `entity_key` string, so every handler that receives one
    looks the row up fresh. `None` when the entity vanished (should not
    happen in practice; `entities` rows are never deleted) or the key
    doesn't parse."""
    row = await deps.pool.fetchrow("SELECT id FROM entities WHERE entity_key = $1", entity_key)
    if row is None:
        return None
    try:
        key = EntityKey.parse(entity_key)
    except ValueError:
        return None
    return int(row["id"]), key


async def task_llm_cost(deps: HandlerDeps, task_id: int) -> float:
    """`HandlerResult.cost_usd` only needs to feed the run's own dollar
    budget cap (`api.executor.budget.BudgetTracker`) — `llm_calls` (Phase 05)
    is already the authoritative cost ledger, so this reads it back by
    `task_id` rather than threading a running total through every extraction
    call site."""
    value = await deps.pool.fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE task_id = $1", task_id
    )
    return float(value)


async def fetch_and_extract(
    deps: HandlerDeps,
    ctx: TaskContext,
    *,
    entity_id: int,
    url: str,
    path: str,
    retrieval_reason: str,
) -> int:
    """Fetch one page, extract claims, persist them. Returns the number of
    claims persisted. A `FetchError`/`BudgetExhaustedError`/
    `LLMValidationError` here is a per-page miss, not a handler failure —
    logged and swallowed, per the phase doc's "partial failure is normal"
    rule; the handler as a whole still succeeds with whatever it got."""
    try:
        deps.retrieval_budget.spend_fetch()
    except BudgetExhaustedError as exc:
        logger.info("tasks.fetch_budget_exhausted", url=url, error=str(exc))
        return 0

    try:
        outcome = await fetch_source(
            deps.pool,
            deps.http,
            deps.throttle,
            deps.robots,
            url,
            retrieval_reason=retrieval_reason,
            force=deps.force_fetch,
        )
    except FetchError as exc:
        logger.info("tasks.fetch_failed", url=url, error=str(exc))
        deps.stats.record_fetch(cache_hit=False)
        return 0

    deps.stats.record_fetch(cache_hit=outcome.cache_outcome is CacheOutcome.HIT)
    source = outcome.source
    if source.http_status != 200 or not source.extracted_text:
        return 0

    llm_ctx = deps.llm_context(ctx.task_id)
    try:
        result = await extract_claims(source, ctx=llm_ctx)
    except LLMValidationError as exc:
        logger.warning("tasks.extraction_failed", url=url, error=str(exc))
        return 0
    deps.stats.record_extraction(result.metrics)
    if not result.claims:
        return 0

    grade = grade_for(classify_own_domain_fetch(path))
    persisted = await persist_extracted_claims(
        deps.pool,
        run_id=deps.run_id,
        entity_id=entity_id,
        source=source,
        claims=result.claims,
        grade=grade,
    )
    return len(persisted)


class ProfileProductHandler:
    kind = TaskKind.PROFILE_PRODUCT.value
    cost_weight = TASK_COST_WEIGHT[TaskKind.PROFILE_PRODUCT]
    service: ServiceName = "crawl"
    timeout_s = 90.0

    def __init__(self, deps: HandlerDeps) -> None:
        self._deps = deps

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        deps = self._deps
        entity_key = str(args["entity_key"])
        resolved = await resolve_entity_id(deps, entity_key)
        if resolved is None:
            logger.warning("profile.unknown_entity", entity_key=entity_key)
            return HandlerResult()
        entity_id, key = resolved
        if key.scheme is not EntityScheme.WEB:
            # gh:/npm:/pypi:/... entities have no domain to profile; those
            # get their own handlers (oss_profile) spawned instead — see
            # discover.py's `_spawn_for_entity`.
            return HandlerResult()

        root_key = key.value
        claims_total = await fetch_and_extract(
            deps,
            ctx,
            entity_id=entity_id,
            url=f"https://{root_key}",
            path="/",
            retrieval_reason="profile_product",
        )

        extra_pages = max(deps.max_pages_per_entity - 1, 0)
        for path_kind in PATH_KINDS_FULL[:extra_pages]:
            guess = await guess_path(
                deps.pool,
                deps.http,
                deps.throttle,
                deps.robots,
                root_key,
                path_kind,
                retrieval_reason="profile_product",
            )
            if guess.found_path is None:
                continue
            claims_total += await fetch_and_extract(
                deps,
                ctx,
                entity_id=entity_id,
                url=f"https://{root_key}{guess.found_path}",
                path=guess.found_path,
                retrieval_reason="profile_product",
            )

        cost_usd = await task_llm_cost(deps, ctx.task_id)
        return HandlerResult(cost_usd=cost_usd, artifacts={"claims_persisted": claims_total})


__all__ = [
    "PATH_KINDS_FULL",
    "ProfileProductHandler",
    "fetch_and_extract",
    "resolve_entity_id",
    "task_llm_cost",
]
