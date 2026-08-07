"""Shared dependencies and run-level instrumentation for task handlers
(Phase 10 phase doc, "Handler shape").

`HandlerDeps` is the one object every handler is constructed with — it
bundles every external-boundary client the seven handlers need (Postgres
pool, HTTP client, per-host throttle/robots, every Phase 04 retriever, the
Phase 04 `SearchRouter`, and a factory for a fresh Phase 05 `LLMContext` per
task) so handlers themselves stay thin (phase doc: "Handlers are thin. All
the difficult logic lives in the layers below"). `RunStats` is a plain
mutable counter bag, shared across every handler instance for one run, that
the CLI reads after `run.finished` to print the phase doc's own
instrumentation table — it exists because none of `HandlerResult.artifacts`,
cache-hit/miss, or entity-verification outcomes are otherwise observable
after the fact (entities are a global table with no `run_id`, and the
executor never persists `HandlerResult.artifacts`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg
import httpx

from api.extract.metrics import ExtractionMetrics
from api.llm.gateway import LLMContext, build_context
from api.models.claims import Grade
from api.resolve.types import VerificationContext
from api.retrieval.fetch import HostThrottle
from api.retrieval.robots import RobotsCache
from api.search.base import SearchResponse
from api.search.budget import RetrievalBudget
from api.search.router import SearchRouter
from api.sources.github import GitHubRetriever
from api.sources.hn import HNRetriever
from api.sources.packages import PackagesRetriever
from api.sources.producthunt import ProductHuntRetriever
from api.sources.reddit import RedditRetriever
from api.sources.stackexchange import StackExchangeRetriever
from api.sources.wayback import WaybackRetriever

# Fallbacks for the still-TBD masterplan §8.2 quota knobs (`Settings.
# max_pages_per_entity`/`max_community_threads` are `None` until Phase 14),
# named the same way `api.planner.registry.DEFAULT_*` already is.
DEFAULT_MAX_PAGES_PER_ENTITY = 4
DEFAULT_MAX_COMMUNITY_THREADS = 10
DEFAULT_MAX_SEARCHES_PER_RUN = 40
DEFAULT_MAX_FETCHES_PER_RUN = 200


@dataclass
class RunStats:
    """Mutated in place by every handler over the life of one run. Not
    persisted — read once by the CLI after `run.finished` (see `cli.py`)."""

    candidates_seen: int = 0
    entities_verified: int = 0
    entities_rejected: int = 0
    insufficient_signal_entities: int = 0

    searches_attempted: int = 0
    search_degraded: int = 0

    fetches_attempted: int = 0
    fetch_cache_hits: int = 0

    claims_bound: int = 0
    claims_dropped: dict[str, int] = field(default_factory=dict)

    llm_extraction_calls: int = 0

    def record_search(self, response: SearchResponse) -> None:
        self.searches_attempted += 1
        if response.degraded:
            self.search_degraded += 1

    def record_fetch(self, *, cache_hit: bool) -> None:
        self.fetches_attempted += 1
        if cache_hit:
            self.fetch_cache_hits += 1

    def record_extraction(self, metrics: ExtractionMetrics) -> None:
        self.llm_extraction_calls += 1
        self.claims_bound += metrics.claims_bound
        for reason, count in metrics.drops.counts.items():
            self.claims_dropped[reason.value] = self.claims_dropped.get(reason.value, 0) + count

    def record_entity(self, *, verified: bool, insufficient_signal: bool = False) -> None:
        self.candidates_seen += 1
        if verified:
            self.entities_verified += 1
            if insufficient_signal:
                self.insufficient_signal_entities += 1
        else:
            self.entities_rejected += 1


@dataclass
class HandlerDeps:
    """Every dependency a Phase 10 task handler needs, constructed once per
    CLI `run` invocation and shared by every handler instance (mirroring
    `api.resolve.types.VerificationContext`'s own "bundle the clients" shape,
    one level up)."""

    pool: asyncpg.Pool
    http: httpx.AsyncClient
    throttle: HostThrottle
    robots: RobotsCache

    github: GitHubRetriever
    hn: HNRetriever
    stackexchange: StackExchangeRetriever
    wayback: WaybackRetriever
    packages: PackagesRetriever
    producthunt: ProductHuntRetriever
    reddit: RedditRetriever

    search_router: SearchRouter
    retrieval_budget: RetrievalBudget

    run_id: str
    llm_api_key: str
    llm_model: str
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str

    max_pages_per_entity: int = DEFAULT_MAX_PAGES_PER_ENTITY
    max_community_threads: int = DEFAULT_MAX_COMMUNITY_THREADS
    force_fetch: bool = False

    stats: RunStats = field(default_factory=RunStats)

    def verification_ctx(self) -> VerificationContext:
        """A fresh `VerificationContext` per call — it's a plain, stateless
        bundle of already-constructed clients (`api.resolve.types`'s own
        docstring), so there's no reason to cache one on `self`."""
        return VerificationContext(
            pool=self.pool,
            http=self.http,
            throttle=self.throttle,
            robots=self.robots,
            github=self.github,
            producthunt=self.producthunt,
        )

    def llm_context(self, task_id: int | None) -> LLMContext:
        return build_context(
            pool=self.pool,
            http_client=self.http,
            api_key=self.llm_api_key,
            model=self.llm_model,
            run_id=self.run_id,
            task_id=task_id,
            langfuse_public_key=self.langfuse_public_key,
            langfuse_secret_key=self.langfuse_secret_key,
            langfuse_host=self.langfuse_host,
        )


# Own-domain fetches and structured API pulls are graded per `api.evidence.grade`;
# these are the fixed grades for the handful of source *kinds* this package
# constructs that aren't "fetch the entity's own site" (masterplan §4.6 table).
COMMUNITY_GRADE = Grade.D
LAUNCH_ANNOUNCEMENT_GRADE = Grade.B  # Product Hunt
AGGREGATOR_GRADE = Grade.C  # SERP-snippet reads of G2/Capterra

__all__ = [
    "AGGREGATOR_GRADE",
    "COMMUNITY_GRADE",
    "DEFAULT_MAX_COMMUNITY_THREADS",
    "DEFAULT_MAX_FETCHES_PER_RUN",
    "DEFAULT_MAX_PAGES_PER_ENTITY",
    "DEFAULT_MAX_SEARCHES_PER_RUN",
    "LAUNCH_ANNOUNCEMENT_GRADE",
    "HandlerDeps",
    "RunStats",
]
