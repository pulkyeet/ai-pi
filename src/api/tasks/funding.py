"""`find_funding` — the hard one (Phase 10 phase doc). Masterplan §5 is
blunt: funding data is "genuinely hard for free", and this handler is
"expected to fail often, and failing is correct behaviour — it reduces
coverage and the report says so."

The masterplan's named substitutes (SEC EDGAR full-text/Form D, UK
Companies House, Wikidata, OpenCorporates) are **not wired up in v1** — each
is its own retriever-shaped integration the size of a Phase 04 module, out
of proportion for a handler the phase doc explicitly allows to fail.
Instead this reuses the same search-then-extract pipeline every other
handler uses: search for the entity's own funding news via
`api.search.router.SearchRouter`, fetch the top real result, and let
`api.extract.extract_claims` look for `company.funding_total_usd`/
`company.stage` in whatever text comes back. A funding page found this way
is third-party reporting, not the entity's own structured data, so it grades
`SourceKind.AGGREGATOR` (C) rather than the A/B an own-domain fetch would get.
"""

from __future__ import annotations

from typing import Any

import structlog

from api.evidence.grade import SourceKind, grade_for
from api.executor.protocol import HandlerResult, ServiceName, TaskContext
from api.extract.extractor import extract_claims
from api.llm.gateway import LLMValidationError
from api.models.claims import ClaimAttribute
from api.models.plan import TASK_COST_WEIGHT, TaskKind
from api.models.source import CacheOutcome
from api.retrieval.errors import FetchError
from api.retrieval.fetch import fetch_source
from api.search.budget import BudgetExhaustedError
from api.tasks.claims import persist_extracted_claims
from api.tasks.context import HandlerDeps
from api.tasks.profile import resolve_entity_id, task_llm_cost

logger = structlog.get_logger()

FUNDING_GRADE = grade_for(SourceKind.AGGREGATOR)
FUNDING_ATTRIBUTES = frozenset(
    {ClaimAttribute.COMPANY_FUNDING_TOTAL.value, ClaimAttribute.COMPANY_STAGE.value}
)


class FindFundingHandler:
    kind = TaskKind.FIND_FUNDING.value
    cost_weight = TASK_COST_WEIGHT[TaskKind.FIND_FUNDING]
    service: ServiceName = "search"
    timeout_s = 60.0

    def __init__(self, deps: HandlerDeps) -> None:
        self._deps = deps

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        deps = self._deps
        entity_key = str(args["entity_key"])
        resolved = await resolve_entity_id(deps, entity_key)
        if resolved is None:
            logger.warning("funding.unknown_entity", entity_key=entity_key)
            return HandlerResult()
        entity_id, key = resolved
        display_name = await deps.pool.fetchval(
            "SELECT display_name FROM entities WHERE id = $1", entity_id
        )

        response = await deps.search_router.search(
            f"{display_name or key.value} funding round investors",
            limit=3,
            budget=deps.retrieval_budget,
        )
        deps.stats.record_search(response)
        cost_usd = response.credits_usd

        claims_total = 0
        for result in response.results[:1]:
            claims_total += await self._try_result(ctx, entity_id=entity_id, url=result.url)

        cost_usd += await task_llm_cost(deps, ctx.task_id)
        return HandlerResult(cost_usd=cost_usd, artifacts={"claims_persisted": claims_total})

    async def _try_result(self, ctx: TaskContext, *, entity_id: int, url: str) -> int:
        deps = self._deps
        try:
            deps.retrieval_budget.spend_fetch()
        except BudgetExhaustedError as exc:
            logger.info("funding.fetch_budget_exhausted", url=url, error=str(exc))
            return 0

        try:
            outcome = await fetch_source(
                deps.pool,
                deps.http,
                deps.throttle,
                deps.robots,
                url,
                retrieval_reason="find_funding",
                force=deps.force_fetch,
            )
        except FetchError as exc:
            logger.info("funding.fetch_failed", url=url, error=str(exc))
            deps.stats.record_fetch(cache_hit=False)
            return 0
        deps.stats.record_fetch(cache_hit=outcome.cache_outcome is CacheOutcome.HIT)

        source = outcome.source
        if source.http_status != 200 or not source.extracted_text:
            return 0

        llm_ctx = deps.llm_context(ctx.task_id)
        try:
            extraction = await extract_claims(source, ctx=llm_ctx)
        except LLMValidationError as exc:
            logger.warning("funding.extraction_failed", url=url, error=str(exc))
            return 0
        deps.stats.record_extraction(extraction.metrics)

        funding_claims = [c for c in extraction.claims if c.attribute in FUNDING_ATTRIBUTES]
        if not funding_claims:
            return 0
        persisted = await persist_extracted_claims(
            deps.pool,
            run_id=deps.run_id,
            entity_id=entity_id,
            source=source,
            claims=funding_claims,
            grade=FUNDING_GRADE,
        )
        return len(persisted)


__all__ = ["FindFundingHandler"]
