"""`discover_competitors` — the fan-out root, and the one handler in this
package with real judgement in it (Phase 10 phase doc). Every other handler
in `api.tasks` is thin; this one is deliberately allowed more logic because
it *is* the planner's "which sources, how many, whether at all" judgement
made concrete, per the phase doc's own framing.

Pipeline, in order:

1. Search every `query_variants` entry via `api.search.router.SearchRouter`
   (budget-bounded).
2. Optionally widen with GitHub repo search for the category itself
   (`consider_oss`) — real OSS products, never `awesome-*` curated lists (a
   list is not a product; Phase 14's q01/q03 finding, fixed here).
3. Turn every raw search hit into a candidate `web:`/`gh:` entity key,
   filtering out aggregator/platform/social domains that can never
   legitimately be a competitor entity.
4. Resolve every surviving candidate through `api.resolve.resolve_entity` —
   this is where masterplan Rule 2 actually fires: a candidate with no real
   public artifact is dropped here, structurally, not by a ranking heuristic.
5. Rank verified entities deterministically (frequency across result sets,
   search rank, non-hobby maturity) and spawn `profile_product` (full
   profile), `extract_pricing` (a cheaper pricing-only pass for the next
   tier down), `oss_profile` (for `gh:`-canonical entities), and
   `find_funding` (`consider_funding`) children, bounded by
   `max_profile_count`.

Deliberately no LLM call anywhere in this handler: a model doing the ranking
would be a new hallucination surface with no artifact check behind it
(phase doc, Open Decision #1) — ranking is plain arithmetic over already-
verified candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from api.executor.protocol import HandlerResult, ServiceName, SpawnRequest, TaskContext
from api.models.entity import EntityScheme, Maturity
from api.models.plan import TASK_COST_WEIGHT, TaskKind
from api.planner.registry import DEFAULT_MAX_COMPETITORS_PROFILED
from api.resolve import EntityEvidence, resolve_entity
from api.resolve.store import Entity
from api.sources.base import RetrieverUnavailableError
from api.tasks.context import HandlerDeps

logger = structlog.get_logger()

# Aggregator/platform/social domains a search result can legitimately point
# at without that page ever being a *competitor entity* in its own right.
# Distinct from `api.retrieval.robots.NO_CRAWL_DOMAINS` (those are never
# crawled at all; these are perfectly crawlable, just never candidates).
NON_CANDIDATE_DOMAINS: frozenset[str] = frozenset(
    {
        "g2.com",
        "capterra.com",
        "getapp.com",
        "trustradius.com",
        "alternativeto.net",
        "producthunt.com",
        "crunchbase.com",
        "news.ycombinator.com",
        "reddit.com",
        "stackoverflow.com",
        "stackexchange.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "wikipedia.org",
        "en.wikipedia.org",
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "medium.com",
        "github.com",  # the bare host; a github.com/owner/repo path is handled separately
        "google.com",
    }
)

# GitHub org/user-less paths that are never a real owner/repo.
_GH_NON_REPO_OWNERS = frozenset({"orgs", "sponsors", "topics", "search", "about", "marketplace"})
_GH_REPO_PATH_RE = re.compile(r"^github\.com/([^/]+)/([^/]+)")

# A GitHub repo that is a curated *list*, not a product, can never be a
# competitor entity. `awesome-*` list repos (and "curated list of ..."-style
# descriptions) are collections of a hundred projects — profiling one as a
# "competitor" is meaningless, and Phase 14's q01/q03 proved it: both
# consumer-role queries engaged GitHub, spawned `oss_profile` against
# awesome-* lists, and shipped zero real competitors. Filtered mechanically
# here so the LLM planner's `consider_oss` judgement can never seed one,
# whatever the prompt says.
_GH_LIST_NAME_RE = re.compile(r"^awesome-", re.IGNORECASE)
_GH_LIST_DESC_RE = re.compile(
    r"curated list|awesome list|collection of (awesome|useful|best)|"
    r"list of (the )?(awesome|best|useful)",
    re.IGNORECASE,
)


def _is_github_list_repo(full_name: str, description: str | None) -> bool:
    repo_name = full_name.split("/", 1)[-1]
    return bool(_GH_LIST_NAME_RE.match(repo_name)) or bool(
        description is not None and _GH_LIST_DESC_RE.search(description)
    )


# Exa's cost is flat per query regardless of result count (docs/external_apis.md's
# search bake-off), so widening this is free credit-wise. Raised from the
# provider default of 10 (Phase 14 finding): household-name competitors for
# mainstream categories consistently ranked outside Exa's top 10 on real
# tuning runs (e.g. "project management tool" surfaced Basecamp/OpenProject,
# not Asana/Trello/ClickUp, even in Phase 01's own bake-off) — a wider result
# window gives them a chance to be seen at all before `_RankedEntity`'s own
# frequency/rank scoring ever gets a say.
DISCOVERY_SEARCH_LIMIT = 20

# Verification is a real network call per candidate — bound how many raw
# search hits ever reach it, independent of how many distinct domains a
# noisy result set produced.
MAX_CANDIDATES_VERIFIED = 24
# Beyond `max_profile_count`, the next tier of ranked survivors gets the
# cheaper extract_pricing pass instead of a full profile.
PRICING_ONLY_MULTIPLIER = 1
# How many of the top-ranked entities get a find_funding child when the
# planner flagged funding as relevant for this category.
MAX_FUNDING_CANDIDATES = 3


@dataclass
class _RawHit:
    display_name: str
    best_rank: int
    hit_count: int = 0
    # First non-empty search-result snippet seen for this candidate — the
    # raw material for the 06b "quote Exa" grade-C evidence path (see
    # `profile_product`'s `extract_snippet_claims`).
    snippet: str = ""


def _registrable_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if ":" in host:
        host = host.split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def _is_non_candidate(host: str) -> bool:
    return host in NON_CANDIDATE_DOMAINS or any(
        host.endswith(f".{d}") for d in NON_CANDIDATE_DOMAINS
    )


def candidate_from_url(url: str, title: str) -> tuple[EntityScheme, str, str] | None:
    """`None` when `url` can never be a competitor entity (aggregator,
    platform, social, or unparseable). A `github.com/<owner>/<repo>` URL
    becomes a `gh:` candidate; everything else on a real registrable domain
    becomes a `web:` candidate keyed on the bare host."""
    parsed = urlparse(url)
    host = _registrable_host(url)
    if not host:
        return None

    joined = f"{host}{parsed.path}".rstrip("/")
    gh_match = _GH_REPO_PATH_RE.match(joined)
    if gh_match:
        owner, repo = gh_match.group(1), gh_match.group(2)
        if owner.lower() in _GH_NON_REPO_OWNERS:
            return None
        return EntityScheme.GH, f"{owner}/{repo}", title or f"{owner}/{repo}"

    if _is_non_candidate(host):
        return None
    return EntityScheme.WEB, host, title or host


@dataclass
class _RankedEntity:
    entity: Entity
    hit_count: int
    best_rank: int
    snippet: str = ""

    @property
    def score(self) -> float:
        maturity_bonus = 2.0 if self.entity.maturity != Maturity.HOBBY else 0.0
        return self.hit_count * 3.0 - self.best_rank * 0.1 + maturity_bonus


def _spawn(
    *, node_key: str, kind: TaskKind, args: dict[str, object], budget_weight: int | None = None
) -> SpawnRequest:
    return SpawnRequest(
        node_key=node_key,
        kind=kind.value,
        args=args,
        budget_weight=budget_weight or TASK_COST_WEIGHT[kind],
    )


class DiscoverCompetitorsHandler:
    kind = TaskKind.DISCOVER_COMPETITORS.value
    cost_weight = TASK_COST_WEIGHT[TaskKind.DISCOVER_COMPETITORS]
    service: ServiceName = "search"
    timeout_s = 120.0

    def __init__(self, deps: HandlerDeps) -> None:
        self._deps = deps

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        deps = self._deps
        query_variants = [str(q) for q in (args.get("query_variants") or [])]
        max_profile_count = int(args.get("max_profile_count") or DEFAULT_MAX_COMPETITORS_PROFILED)
        consider_oss = bool(args.get("consider_oss", False))
        consider_funding = bool(args.get("consider_funding", False))

        cost_usd = 0.0
        raw_hits: dict[tuple[EntityScheme, str], _RawHit] = {}

        for rank_offset, variant in enumerate(query_variants):
            response = await deps.search_router.search(
                variant, limit=DISCOVERY_SEARCH_LIMIT, budget=deps.retrieval_budget
            )
            deps.stats.record_search(response)
            cost_usd += response.credits_usd
            for result in response.results:
                candidate = candidate_from_url(result.url, result.title)
                if candidate is None:
                    continue
                scheme, raw_value, name = candidate
                key = (scheme, raw_value)
                hit = raw_hits.get(key)
                rank = result.rank + rank_offset * 1000  # later variants rank strictly worse
                if hit is None:
                    raw_hits[key] = _RawHit(
                        display_name=name, best_rank=rank, hit_count=1, snippet=result.snippet
                    )
                else:
                    hit.hit_count += 1
                    hit.best_rank = min(hit.best_rank, rank)
                    if not hit.snippet and result.snippet:
                        hit.snippet = result.snippet

        if consider_oss and query_variants:
            await self._seed_github_repos(query_variants[0], raw_hits)

        ranked_raw = sorted(raw_hits.items(), key=lambda kv: (-kv[1].hit_count, kv[1].best_rank))[
            :MAX_CANDIDATES_VERIFIED
        ]

        entities_by_id: dict[int, _RankedEntity] = {}
        for (scheme, raw_value), hit in ranked_raw:
            evidence = EntityEvidence(
                scheme=scheme, raw_value=raw_value, display_name=hit.display_name
            )
            try:
                entity = await resolve_entity(deps.verification_ctx(), evidence)
            except (httpx.HTTPError, OSError) as exc:
                # A real, live-run finding: `api.retrieval.fetch_source`
                # only wraps timeouts in a typed `FetchError` (Phase 03);
                # a raw transport failure (DNS `NameResolutionError`,
                # connection reset, ...) for one bad candidate — a search
                # result naming a domain that no longer resolves, say — is
                # not, and would otherwise crash this entire task instead
                # of just dropping the one candidate it broke on. One
                # candidate failing is exactly the "partial failure is
                # normal" case (phase doc), never a reason to fail
                # discovery outright.
                logger.info(
                    "discover.candidate_resolution_failed",
                    scheme=scheme.value,
                    raw_value=raw_value,
                    error=str(exc),
                )
                entity = None
            deps.stats.record_entity(
                verified=entity is not None,
                insufficient_signal=bool(
                    entity is not None and entity.meta.get("insufficient_signal")
                ),
            )
            if entity is None:
                continue
            existing = entities_by_id.get(entity.id)
            if existing is None or hit.hit_count > existing.hit_count:
                entities_by_id[entity.id] = _RankedEntity(
                    entity=entity,
                    hit_count=hit.hit_count,
                    best_rank=hit.best_rank,
                    snippet=hit.snippet,
                )

        ranked = sorted(entities_by_id.values(), key=lambda r: r.score, reverse=True)

        spawned: list[SpawnRequest] = []
        profiled = ranked[:max_profile_count]
        pricing_only = ranked[max_profile_count : max_profile_count * (1 + PRICING_ONLY_MULTIPLIER)]

        for ranked_entity in profiled:
            spawned.append(
                self._spawn_for_entity(
                    ranked_entity.entity, full_profile=True, snippet=ranked_entity.snippet
                )
            )
        for ranked_entity in pricing_only:
            spawned.append(self._spawn_for_entity(ranked_entity.entity, full_profile=False))

        if consider_funding:
            for ranked_entity in ranked[:MAX_FUNDING_CANDIDATES]:
                spawned.append(
                    _spawn(
                        node_key=f"funding:{ranked_entity.entity.entity_key}",
                        kind=TaskKind.FIND_FUNDING,
                        args={"entity_key": ranked_entity.entity.entity_key},
                    )
                )

        return HandlerResult(
            cost_usd=cost_usd,
            artifacts={
                "candidates_seen": len(raw_hits),
                "entities_verified": len(entities_by_id),
                "spawned": len(spawned),
            },
            spawned=spawned,
        )

    def _spawn_for_entity(
        self, entity: Entity, *, full_profile: bool, snippet: str = ""
    ) -> SpawnRequest:
        scheme = entity.entity_key.split(":", 1)[0]
        if scheme == EntityScheme.GH.value:
            repo = entity.entity_key.split(":", 1)[1]
            return _spawn(
                node_key=f"oss:{entity.entity_key}", kind=TaskKind.OSS_PROFILE, args={"repo": repo}
            )
        kind = TaskKind.PROFILE_PRODUCT if full_profile else TaskKind.EXTRACT_PRICING
        prefix = "profile" if full_profile else "pricing"
        args: dict[str, object] = {"entity_key": entity.entity_key}
        if full_profile and snippet:
            # 06b "quote Exa": the search-result snippet becomes a grade-C
            # synthetic source `profile_product` extracts claims from — cheap
            # evidence for an entity even where the real pages fall short.
            args["snippet"] = snippet
        return _spawn(node_key=f"{prefix}:{entity.entity_key}", kind=kind, args=args)

    async def _seed_github_repos(
        self, query: str, raw_hits: dict[tuple[EntityScheme, str], _RawHit]
    ) -> None:
        """Best-effort OSS widening: search GitHub for real repos in the
        category itself (star-sorted), never `awesome-*` curated lists. A
        search failure (rate limit, transport error) never fails discovery,
        it just means one fewer seed strategy contributed this run."""
        try:
            hits = await self._deps.github.search_repositories(
                f"{query} in:name,description stars:>100"
            )
        except (httpx.HTTPError, RetrieverUnavailableError) as exc:
            logger.info("discover.github_repo_search_failed", query=query, error=str(exc))
            return
        for rank, repo_hit in enumerate(hits):
            if _is_github_list_repo(repo_hit.full_name, repo_hit.description):
                continue
            key = (EntityScheme.GH, repo_hit.full_name)
            existing = raw_hits.get(key)
            if existing is None:
                raw_hits[key] = _RawHit(
                    display_name=repo_hit.full_name, best_rank=rank, hit_count=2
                )
            else:
                existing.hit_count += 2
                existing.best_rank = min(existing.best_rank, rank)


__all__ = ["DiscoverCompetitorsHandler", "candidate_from_url"]
