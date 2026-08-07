"""`trend_signals` — Wikipedia pageviews plus HN post volume over time
(Phase 10 phase doc; masterplan §5's Trend row: "better than pytrends, which
breaks constantly").

**A real, undecided gap, surfaced rather than papered over**: the masterplan
§4.4 closed claim vocabulary has no attribute for trend/volume data at all —
nothing like `trend.<keyword>.volume` exists next to `complaint.<theme>` or
`oss.stars`. Extending `ClaimAttribute` (a frozen Phase 00 contract) is not
this phase's call to make unilaterally. So, unlike every other handler in
this package, `trend_signals` **persists no claims** — it reports what it
found via `HandlerResult.artifacts` only, which the executor does not
persist anywhere durable either (see `context.py`'s module docstring). This
means trend data collected here does not currently survive past the run
that collected it; it is real signal with nowhere in the schema to land.
Left as an explicit open item for Phase 11 (which would need somewhere to
put it in the report) rather than guessed at here.

Wikipedia's pageviews REST API has no per-repo/domain retriever in
`api.sources` (it is the only call site), so it is called directly with
`httpx` here rather than adding a single-method module one layer down for
one caller — kept small and self-contained, mirroring how `api.sources.
serp_snippets` stays a thin, single-purpose module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from api.executor.protocol import HandlerResult, ServiceName, TaskContext
from api.models.plan import TASK_COST_WEIGHT, TaskKind
from api.tasks.context import HandlerDeps

logger = structlog.get_logger()

WIKIPEDIA_PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/all-agents/{article}/monthly/{start}/{end}"
)
PAGEVIEWS_WINDOW_DAYS = 365
MAX_KEYWORDS = 5


def _wikipedia_article_guess(keyword: str) -> str:
    words = keyword.strip().split()
    return "_".join(w.capitalize() for w in words)


class TrendSignalsHandler:
    kind = TaskKind.TREND_SIGNALS.value
    cost_weight = TASK_COST_WEIGHT[TaskKind.TREND_SIGNALS]
    service: ServiceName = "search"
    timeout_s = 30.0

    def __init__(self, deps: HandlerDeps) -> None:
        self._deps = deps

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        deps = self._deps
        keywords = [str(k) for k in (args.get("keywords") or [])][:MAX_KEYWORDS]
        if not keywords:
            return HandlerResult()

        hn_volume: dict[str, int] = {}
        for keyword in keywords:
            try:
                hits = await deps.hn.search(keyword, by_date=True)
            except httpx.HTTPError as exc:
                logger.info("trends.hn_search_failed", keyword=keyword, error=str(exc))
                continue
            hn_volume[keyword] = len(hits)

        pageviews: dict[str, int | None] = {}
        for keyword in keywords:
            pageviews[keyword] = await self._pageviews(keyword)

        return HandlerResult(
            cost_usd=0.0,
            artifacts={"hn_post_volume": hn_volume, "wikipedia_monthly_pageviews": pageviews},
        )

    async def _pageviews(self, keyword: str) -> int | None:
        article = _wikipedia_article_guess(keyword)
        end = datetime.now(UTC)
        start = end - timedelta(days=PAGEVIEWS_WINDOW_DAYS)
        url = WIKIPEDIA_PAGEVIEWS_URL.format(
            article=article, start=start.strftime("%Y%m01"), end=end.strftime("%Y%m01")
        )
        try:
            resp = await self._deps.http.get(url, timeout=10.0)
        except httpx.HTTPError as exc:
            logger.info("trends.wikipedia_pageviews_failed", article=article, error=str(exc))
            return None
        if resp.status_code != 200:
            return None
        try:
            items = resp.json().get("items", [])
        except ValueError:
            return None
        return sum(int(item.get("views", 0)) for item in items) or None


__all__ = ["TrendSignalsHandler"]
