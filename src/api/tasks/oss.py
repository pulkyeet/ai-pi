"""`oss_profile` — GitHub API: stars, 90-day star velocity, last commit,
license (Phase 10 phase doc). All grade A structured data, per masterplan
§4.6. Planner-gated (`consider_oss`) — spawned by `discover_competitors` for
every `gh:`-canonical entity, never seeded directly (`api.planner.registry.
SEED_KINDS`).

**No LLM call, no `bind_span` over prose** — these are exact numbers read
straight off a structured API, not something extracted from a page. The
`claims` schema still requires `quote`/`char_start`/`char_end`/`source_id`
on every row (masterplan Rule 1 applies uniformly), so this builds a short
synthetic "page" summarising the API response and binds a quote it wrote
itself against that same text (`api.tasks.claims.persist_structured_claim`)
— deterministic, and can never hit a span-binding drop by construction.

`oss.contributors_90d` is **not populated**: `GitHubRetriever.repo_metadata`
only exposes a *total* contributor count (via the `contributors` endpoint's
Link-header trick), not one scoped to the trailing 90 days — no GitHub
endpoint Phase 04 wired up provides that distinction. Persisting the total
under a `_90d` attribute name would misrepresent it; left as an open gap for
a future retriever addition. `oss.stars_90d_delta` is an approximation —
`compute_star_velocity`'s stars-per-day rate times 90, since the Starring
endpoint (the only source of exact per-star timestamps) still 403s under the
current fine-grained PAT (open since Phase 01/04) and this handler degrades
that one field to "unknown" rather than failing the whole task.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from api.evidence.grade import SourceKind, grade_for
from api.executor.protocol import HandlerResult, ServiceName, TaskContext
from api.models.claims import ClaimAttribute
from api.models.plan import TASK_COST_WEIGHT, TaskKind
from api.resolve.maturity import MaturitySignals, derive_maturity
from api.resolve.store import upsert_entity
from api.sources.base import RetrieverUnavailableError
from api.tasks.claims import get_or_create_synthetic_source, persist_structured_claim
from api.tasks.context import HandlerDeps
from api.tasks.profile import resolve_entity_id

logger = structlog.get_logger()

STRUCTURED_GRADE = grade_for(SourceKind.STRUCTURED_API)


def _summary_text(
    repo: str, *, stars: int, license_: str | None, last_commit: datetime | None
) -> str:
    lines = [
        f"{repo}: {stars} stars.",
        f"License: {license_ or 'none declared'}.",
    ]
    if last_commit is not None:
        lines.append(f"Last commit at {last_commit.date().isoformat()}.")
    return "\n".join(lines) + "\n"


class OssProfileHandler:
    kind = TaskKind.OSS_PROFILE.value
    cost_weight = TASK_COST_WEIGHT[TaskKind.OSS_PROFILE]
    service: ServiceName = "crawl"
    timeout_s = 30.0

    def __init__(self, deps: HandlerDeps) -> None:
        self._deps = deps

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        deps = self._deps
        repo = str(args["repo"])
        if "/" not in repo:
            logger.warning("oss.malformed_repo", repo=repo)
            return HandlerResult()
        owner, name = repo.split("/", 1)

        resolved = await resolve_entity_id(deps, f"gh:{repo}")
        if resolved is None:
            logger.warning("oss.unknown_entity", repo=repo)
            return HandlerResult()
        entity_id, _key = resolved

        try:
            gh_repo = await deps.github.repo_metadata(owner, name)
        except (httpx.HTTPError, RetrieverUnavailableError) as exc:
            logger.info("oss.repo_metadata_failed", repo=repo, error=str(exc))
            return HandlerResult()

        velocity: float | None = None
        try:
            velocity = await deps.github.star_velocity_90d(owner, name)
        except (httpx.HTTPError, RetrieverUnavailableError) as exc:
            logger.info("oss.star_velocity_unavailable", repo=repo, error=str(exc))

        text = _summary_text(
            repo,
            stars=gh_repo.stargazers_count,
            license_=gh_repo.license,
            last_commit=gh_repo.last_commit_at,
        )
        source = await get_or_create_synthetic_source(
            deps.pool,
            canonical_url=f"github-api://{repo}",
            root_key="github.com",
            text=text,
            retrieval_reason="oss_profile",
        )

        claims_total = 0
        claims_total += await self._persist(
            source,
            entity_id=entity_id,
            attribute=ClaimAttribute.OSS_REPO,
            quote=repo,
            value_text=repo,
        )
        claims_total += await self._persist(
            source,
            entity_id=entity_id,
            attribute=ClaimAttribute.OSS_STARS,
            quote=f"{gh_repo.stargazers_count} stars",
            value_num=float(gh_repo.stargazers_count),
        )
        if gh_repo.license:
            claims_total += await self._persist(
                source,
                entity_id=entity_id,
                attribute=ClaimAttribute.OSS_LICENSE,
                quote=f"License: {gh_repo.license}",
                value_text=gh_repo.license,
            )
        if gh_repo.last_commit_at is not None:
            claims_total += await self._persist(
                source,
                entity_id=entity_id,
                attribute=ClaimAttribute.OSS_LAST_COMMIT_AT,
                quote=f"Last commit at {gh_repo.last_commit_at.date().isoformat()}",
                value_text=gh_repo.last_commit_at.date().isoformat(),
            )
        if velocity is not None:
            claims_total += await self._persist(
                source,
                entity_id=entity_id,
                attribute=ClaimAttribute.OSS_STARS_90D_DELTA,
                quote=f"{gh_repo.stargazers_count} stars",
                value_num=round(velocity * 90),
            )

        await self._refresh_maturity(repo, gh_repo=gh_repo, velocity=velocity)

        return HandlerResult(cost_usd=0.0, artifacts={"claims_persisted": claims_total})

    async def _persist(
        self,
        source: object,
        *,
        entity_id: int,
        attribute: ClaimAttribute,
        quote: str,
        value_text: str | None = None,
        value_num: float | None = None,
    ) -> int:
        claim_id = await persist_structured_claim(
            self._deps.pool,
            run_id=self._deps.run_id,
            entity_id=entity_id,
            source=source,  # type: ignore[arg-type]
            attribute=attribute.value,
            quote=quote,
            value_text=value_text,
            value_num=value_num,
            grade=STRUCTURED_GRADE,
        )
        return 1 if claim_id is not None else 0

    async def _refresh_maturity(
        self, repo: str, *, gh_repo: object, velocity: float | None
    ) -> None:
        """Opportunistic enrichment: `discover_competitors` resolved this
        entity with no signals at all (nothing was known yet); now that
        real GitHub data exists, recompute maturity and overwrite it —
        `store.upsert_entity` always takes the freshest classification
        (`docs/tracker.md`'s Phase 07 Next Steps note 10(a))."""
        stars = getattr(gh_repo, "stargazers_count", None)
        last_commit_at = getattr(gh_repo, "last_commit_at", None)
        signals = MaturitySignals(
            stars=stars, last_commit_at=last_commit_at, star_velocity_90d=velocity
        )
        assignment = derive_maturity(signals, now=datetime.now(UTC))
        await upsert_entity(
            self._deps.pool,
            entity_key=f"gh:{repo}",
            display_name=repo,
            maturity=assignment.tier,
            meta={
                "maturity_rule": assignment.rule,
                "insufficient_signal": assignment.insufficient_signal,
            },
        )


__all__ = ["OssProfileHandler"]
