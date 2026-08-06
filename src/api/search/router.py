"""Provider routing, allowance tracking, and degradation (masterplan §7/§9).

`SearchRouter.search()` is the only entry point task handlers call. Three
independent gates, in order:

  1. **`RetrievalBudget`** (if passed) — an in-memory per-run call-count cap.
     Exhaustion raises `api.search.budget.BudgetExhaustedError` straight out of
     this call; that's the caller's problem to convert into a coverage gap
     (see `api.search.budget`'s own docstring). Charged even on what turns
     out to be a cache hit — the budget bounds *search attempts*, not spend.
  2. **The 24h cache** (`api.search.cache`) — a hit never touches the
     network or the credit ledger.
  3. **The credit allowance** — `search_credit_usage` summed over a rolling
     24h window against `daily_credit_cap_usd`. Both this and any real
     provider failure (`ProviderError`, e.g. Exa 5xx/timeout after retries)
     are caught *inside* this method and turned into a degraded
     `SearchResponse` rather than raised. This is deliberate: "degradation
     is a designed path, not an error path" (phase doc) — an Exa outage or
     an exhausted allowance should leave a run running on domain retrievers
     with a marked coverage gap, never crash it.

"Daily" and "global daily" (phase doc's two allowance tiers) collapse to one
check here: `search_credit_usage` is already a system-wide ledger, not
per-run or per-user, so a single rolling-24h sum covers both. Both are
`None` (unenforced) until Phase 14 measures real credits/run.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import structlog

from api.search import cache as search_cache
from api.search.base import ProviderError, SearchProvider, SearchResponse
from api.search.budget import RetrievalBudget

logger = structlog.get_logger()


class AllowanceExhaustedError(Exception):
    def __init__(self, cap_usd: float, spent_usd: float) -> None:
        super().__init__(f"credit allowance exhausted: spent ${spent_usd:.4f} of ${cap_usd:.4f}")
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd


async def credits_spent_rolling_24h(pool: asyncpg.Pool, provider: str) -> float:
    value = await pool.fetchval(
        "SELECT COALESCE(SUM(credits_usd), 0) FROM search_credit_usage "
        "WHERE provider = $1 AND spent_at >= now() - interval '24 hours'",
        provider,
    )
    return float(value)


class SearchRouter:
    def __init__(
        self,
        pool: asyncpg.Pool,
        provider: SearchProvider,
        *,
        run_id: str,
        daily_credit_cap_usd: float | None = None,
    ) -> None:
        self._pool = pool
        self._provider = provider
        self._run_id = run_id
        self._daily_credit_cap_usd = daily_credit_cap_usd

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        site: str | None = None,
        budget: RetrievalBudget | None = None,
    ) -> SearchResponse:
        if budget is not None:
            budget.spend_search(self._provider.name)

        params: dict[str, Any] = {"limit": limit, "site": site}
        key = search_cache.cache_key(query, self._provider.name, params)

        cached = await search_cache.get_fresh(self._pool, key)
        if cached is not None:
            return cached

        try:
            await self._check_allowance()
            response = await self._provider.search(query, limit=limit, site=site)
        except (ProviderError, AllowanceExhaustedError) as exc:
            logger.warning(
                "search.degraded", provider=self._provider.name, query=query, reason=str(exc)
            )
            return SearchResponse(
                provider=self._provider.name, degraded=True, degradation_reason=str(exc)
            )

        await self._record_credit_usage(response.credits_usd)
        await search_cache.upsert(
            self._pool,
            key=key,
            provider=self._provider.name,
            query=query,
            params=params,
            response=response,
        )
        return response

    async def _check_allowance(self) -> None:
        if self._daily_credit_cap_usd is None:
            return
        spent = await credits_spent_rolling_24h(self._pool, self._provider.name)
        if spent >= self._daily_credit_cap_usd:
            raise AllowanceExhaustedError(self._daily_credit_cap_usd, spent)

    async def _record_credit_usage(self, credits_usd: float) -> None:
        await self._pool.execute(
            "INSERT INTO search_credit_usage (provider, run_id, credits_usd) VALUES ($1, $2, $3)",
            self._provider.name,
            self._run_id,
            credits_usd,
        )


__all__ = ["AllowanceExhaustedError", "SearchRouter", "credits_spent_rolling_24h"]
