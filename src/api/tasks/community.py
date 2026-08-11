"""`mine_community` — HN Algolia, GitHub issue search, and Stack Exchange,
emitting `complaint.<theme>`/`request.<theme>` claims via the same LLM
extraction + span-binding pipeline every other handler uses (Phase 10 phase
doc). Bounded by `HandlerDeps.max_community_threads`.

**A real schema/vocabulary tension, resolved here.** The masterplan's own
output contract (§2) has `pain_points`/`feature_gaps` with no `entity_key`
at all — community complaints are category-wide signal, not attributed to
one competitor. But `claims.entity_id` is `NOT NULL` (Phase 00, frozen), and
inventing a new `EntityScheme` to represent "no particular entity" would be
an unmandated change to that same frozen contract. Every claim this handler
persists is therefore attached to one synthetic **per-run** bookkeeping
entity, `category:<run_id>` — built directly via `api.resolve.store.
upsert_entity`, never routed through `resolve_entity` (so it never has to,
and never could, pass masterplan Rule 2's artifact-verification check, and
is never intended to appear in a report's `competitors` list). `entity_id`
on a `complaint.*`/`request.*` claim is bookkeeping only; Phase 11's theme
clustering groups by `attribute`, not `entity_id` — exactly matching the
Report contract's own entity-agnostic `pain_points`/`feature_gaps` shape.

v1 does not attempt to detect whether a mined comment is actually about one
of this run's already-discovered competitors (which would let a complaint
attach to a real entity when detectable) — noted as an open item rather than
built, partly because the phase doc's own Open Decision #2 ("should
`mine_community` run before profiling?") means there is no ordering
guarantee that any competitor is even resolved yet when this handler runs.
"""

from __future__ import annotations

from typing import Any

import structlog

from api.executor.protocol import HandlerResult, ServiceName, TaskContext
from api.extract.extractor import extract_claims
from api.llm.gateway import LLMValidationError
from api.models.plan import TASK_COST_WEIGHT, TaskKind
from api.resolve.store import upsert_entity
from api.sources.base import RetrieverUnavailableError
from api.tasks.claims import get_or_create_synthetic_source, persist_extracted_claims
from api.tasks.context import COMMUNITY_GRADE, HandlerDeps

logger = structlog.get_logger()

VALID_VENUES = frozenset({"hn", "github", "stackexchange"})


async def category_entity_id(deps: HandlerDeps, *, hint: str) -> int:
    entity = await upsert_entity(
        deps.pool,
        entity_key=f"category:{deps.run_id}",
        display_name=f"Community signal: {hint}"[:200],
        maturity=None,
        meta={"synthetic": True, "kind": "category"},
    )
    return entity.id


async def _hn_lines(deps: HandlerDeps, keyword: str, limit: int) -> list[str]:
    hits = await deps.hn.search(keyword)
    return [
        f'- "{h.title}" ({h.url or "no link"}) — {h.points or 0} points, '
        f"{h.num_comments or 0} comments"
        for h in hits[:limit]
    ]


async def _stackexchange_lines(deps: HandlerDeps, keyword: str, limit: int) -> list[str]:
    hits = await deps.stackexchange.search(keyword, limit=limit)
    lines = []
    for h in hits:
        body = (h.body or "").strip()
        lines.append(f'- "{h.title}" ({h.link}) — score {h.score or 0}. {body}'[:1000])
    return lines


async def _github_lines(deps: HandlerDeps, keyword: str, limit: int) -> list[str]:
    hits = await deps.github.search_issues(keyword, limit=limit)
    return [
        f'- "{h.title}" ({h.html_url}) — {h.reactions_total} reactions, repo {h.repo}' for h in hits
    ]


_VENUE_FETCHERS = {
    "hn": _hn_lines,
    "stackexchange": _stackexchange_lines,
    "github": _github_lines,
}


class MineCommunityHandler:
    kind = TaskKind.MINE_COMMUNITY.value
    cost_weight = TASK_COST_WEIGHT[TaskKind.MINE_COMMUNITY]
    service: ServiceName = "search"
    # A real live-run finding: sequential per-(venue, keyword) HTTP calls
    # plus one LLM extraction call each (never parallelised — see the
    # phase doc's "handlers are thin" rule; concurrency belongs to the
    # executor's own per-service semaphores, not inside a handler) can
    # genuinely exceed 90s across several venues/keywords. 180s observed
    # comfortable in practice; still bounded, not unbounded.
    timeout_s = 180.0

    def __init__(self, deps: HandlerDeps) -> None:
        self._deps = deps

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        deps = self._deps
        keywords = [str(k) for k in (args.get("keywords") or [])]
        venues = [str(v) for v in (args.get("venues") or []) if str(v) in VALID_VENUES]
        if not keywords or not venues:
            return HandlerResult()

        entity_id = await category_entity_id(deps, hint=keywords[0])
        per_call_limit = max(deps.max_community_threads // max(len(keywords) * len(venues), 1), 1)

        claims_total = 0
        for venue in venues:
            fetcher = _VENUE_FETCHERS[venue]
            for keyword in keywords:
                try:
                    lines = await fetcher(deps, keyword, per_call_limit)
                except RetrieverUnavailableError as exc:
                    logger.info("community.venue_unavailable", venue=venue, error=str(exc))
                    continue
                if not lines:
                    continue
                claims_total += await self._extract_and_persist(
                    ctx, entity_id=entity_id, venue=venue, keyword=keyword, lines=lines
                )

        cost_usd = await deps.pool.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE task_id = $1", ctx.task_id
        )
        return HandlerResult(cost_usd=float(cost_usd), artifacts={"claims_persisted": claims_total})

    async def _extract_and_persist(
        self, ctx: TaskContext, *, entity_id: int, venue: str, keyword: str, lines: list[str]
    ) -> int:
        deps = self._deps
        text = (
            f'Community discussion mentioning "{keyword}" on {venue}:\n\n' + "\n".join(lines) + "\n"
        )
        source = await get_or_create_synthetic_source(
            deps.pool,
            canonical_url=f"community://{venue}/{deps.run_id}/{keyword}",
            root_key=venue,
            text=text,
            retrieval_reason="mine_community",
        )
        llm_ctx = deps.llm_context(ctx.task_id)
        try:
            result = await extract_claims(source, ctx=llm_ctx)
        except LLMValidationError as exc:
            logger.warning(
                "community.extraction_failed", venue=venue, keyword=keyword, error=str(exc)
            )
            return 0
        deps.stats.record_extraction(result.metrics)
        if not result.claims:
            return 0
        persisted = await persist_extracted_claims(
            deps.pool,
            run_id=deps.run_id,
            entity_id=entity_id,
            source=source,
            claims=result.claims,
            grade=COMMUNITY_GRADE,
        )
        return len(persisted)


__all__ = ["MineCommunityHandler", "category_entity_id"]
