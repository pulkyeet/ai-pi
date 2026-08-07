"""`extract_pricing` — narrower and cheaper than `profile_product`: pricing
paths only (Phase 10 phase doc). `discover_competitors` spawns this for the
tier of ranked candidates just below `max_profile_count`, to get pricing
signal on more competitors than the full-profile budget allows without
paying for a homepage/docs/changelog fetch on each.
"""

from __future__ import annotations

from typing import Any

import structlog

from api.executor.protocol import HandlerResult, ServiceName, TaskContext
from api.models.entity import EntityScheme
from api.models.plan import TASK_COST_WEIGHT, TaskKind
from api.retrieval.pathguess import guess_path
from api.tasks.context import HandlerDeps
from api.tasks.profile import fetch_and_extract, resolve_entity_id, task_llm_cost

logger = structlog.get_logger()


class ExtractPricingHandler:
    kind = TaskKind.EXTRACT_PRICING.value
    cost_weight = TASK_COST_WEIGHT[TaskKind.EXTRACT_PRICING]
    service: ServiceName = "crawl"
    timeout_s = 60.0

    def __init__(self, deps: HandlerDeps) -> None:
        self._deps = deps

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        deps = self._deps
        entity_key = str(args["entity_key"])
        resolved = await resolve_entity_id(deps, entity_key)
        if resolved is None:
            logger.warning("pricing.unknown_entity", entity_key=entity_key)
            return HandlerResult()
        entity_id, key = resolved
        if key.scheme is not EntityScheme.WEB:
            return HandlerResult()

        root_key = key.value
        guess = await guess_path(
            deps.pool,
            deps.http,
            deps.throttle,
            deps.robots,
            root_key,
            "pricing",
            retrieval_reason="extract_pricing",
        )

        claims_total = 0
        if guess.found_path is not None:
            claims_total = await fetch_and_extract(
                deps,
                ctx,
                entity_id=entity_id,
                url=f"https://{root_key}{guess.found_path}",
                path=guess.found_path,
                retrieval_reason="extract_pricing",
            )

        cost_usd = await task_llm_cost(deps, ctx.task_id)
        return HandlerResult(cost_usd=cost_usd, artifacts={"claims_persisted": claims_total})


__all__ = ["ExtractPricingHandler"]
