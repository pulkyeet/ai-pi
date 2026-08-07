"""Claims -> findings (phase doc's Design section; masterplan §4.3: "`findings`
is the only table whose text ever reaches a user, and it always carries
`claim_ids`" — a database `CHECK`, `findings_must_cite`, since migration
`0001`).

Finding statements at this stage are **templated, not generated** — deterministic
string formatting over real, already-computed numbers (masterplan: "the report
prints the real N — no invented '3,248 comments'"). Prose *generation* (MVP,
feature-gap statements, risks) is `api.synth.generate`'s job, one layer up,
consuming these findings as its only input.

Four kinds, matching the phase doc's table:

| kind                 | built from                                            |
|-----------------------|-------------------------------------------------------|
| `pain_point`          | `complaint.<theme>` claims, clustered                  |
| `feature_gap`         | `request.<theme>` claims, clustered, minus themes with |
|                       | a matching `feature.<slug>.present` claim in the run   |
| `pricing_observation` | `pricing.entry_usd_month` claims across >=2 entities   |
| `competitor`          | resolved (non-synthetic) entities with >=1 claim       |

**A real, carried-forward gap in what "distinct threads" and "GitHub reaction
count" can mean here, named rather than silently approximated.** Masterplan
§4.6's two promotion rules assume per-claim thread identity (Reddit/HN) and
per-issue reaction counts (GitHub) survive to the claim itself. They don't:
`api.tasks.community.MineCommunityHandler` bundles up to
`max_community_threads` real threads/issues from one `(venue, keyword)` pair
into a *single* synthetic source before extraction (see that module's own
docstring), so an individual `complaint.*`/`request.*` claim has no
surviving link back to which real thread or issue it came from — only which
synthetic `(venue, keyword)` source. This module therefore treats **distinct
`source_id` among a cluster's claims** as the thread-breadth proxy for
`api.evidence.promotion.evaluate_community_theme`, uniformly for every venue
including GitHub — `evaluate_github_theme` (reaction-weighted, no breadth
requirement) has no real caller in v1, because no per-issue reaction count
survives to a claim for it to consume. Both are undercounts of true breadth
(many real threads collapse into one synthetic source), never overcounts, so
promotion is conservative in the direction the masterplan's own risk
mitigation prefers ("over-merging is the more damaging error"). Fixing this
properly needs `api.tasks.community` to persist a real per-claim thread/issue
identity — out of this phase's scope; logged in `docs/tracker.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import asyncpg
from pydantic import BaseModel

from api.evidence.promotion import PromotionResult, evaluate_community_theme
from api.llm.embed import EmbedContext
from api.synth.cluster import ThemeCluster, ThemeItem, embed_and_cluster

_GRADE_RANK: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}

MIN_ENTITIES_FOR_PRICING_OBSERVATION = 2

# api.tasks.community.category_entity_id's synthetic per-run bookkeeping
# entity — never a real competitor, never eligible for a `competitor` finding.
_SYNTHETIC_ENTITY_PREFIX = "category:"


class FindingKind(StrEnum):
    PAIN_POINT = "pain_point"
    FEATURE_GAP = "feature_gap"
    PRICING_OBSERVATION = "pricing_observation"
    COMPETITOR = "competitor"


class Finding(BaseModel):
    id: int
    run_id: str
    kind: FindingKind
    statement: str
    claim_ids: list[int]
    support_count: int | None = None
    confidence: float | None = None
    # In-memory-only metadata, never written to the `findings` table (whose
    # schema is frozen at `statement`/`claim_ids`/`support_count`/
    # `confidence` — Phase 00, migration `0001`). `api.synth.assemble`
    # consumes these directly off the objects `build_all_findings` returns
    # in the same call chain, rather than re-deriving them from `statement`
    # by string-parsing a template (which would couple two modules through
    # prose formatting) or re-querying Postgres for data already in hand.
    theme: str | None = None
    distinct_threads: int | None = None
    best_grade: str | None = None


@dataclass(frozen=True)
class FindingDraft:
    kind: FindingKind
    statement: str
    claim_ids: tuple[int, ...]
    support_count: int | None = None
    confidence: float | None = None
    theme: str | None = None
    distinct_threads: int | None = None
    best_grade: str | None = None


def _mean_confidence(confidences: tuple[float, ...]) -> float:
    return sum(confidences) / len(confidences)


async def persist_finding(pool: asyncpg.Pool, run_id: str, draft: FindingDraft) -> Finding:
    row = await pool.fetchrow(
        """
        INSERT INTO findings (run_id, kind, statement, claim_ids, support_count, confidence)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        run_id,
        draft.kind.value,
        draft.statement,
        list(draft.claim_ids),
        draft.support_count,
        draft.confidence,
    )
    assert row is not None
    return Finding(
        id=row["id"],
        run_id=run_id,
        kind=draft.kind,
        statement=draft.statement,
        claim_ids=list(draft.claim_ids),
        support_count=draft.support_count,
        confidence=draft.confidence,
        theme=draft.theme,
        distinct_threads=draft.distinct_threads,
        best_grade=draft.best_grade,
    )


# ---------------------------------------------------------------------------
# pain_point / feature_gap — complaint.*/request.* claims, clustered
# ---------------------------------------------------------------------------


async def _fetch_theme_items(pool: asyncpg.Pool, run_id: str, *, family: str) -> list[ThemeItem]:
    """`family` is `"complaint"` or `"request"`. `request.<theme>.reactions`
    is excluded — it's a numeric companion attribute to a `request.<theme>`
    claim, not a theme claim of its own."""
    rows = await pool.fetch(
        """
        SELECT id, attribute, quote, source_id, grade, confidence
          FROM claims
         WHERE run_id = $1 AND superseded_by IS NULL
           AND attribute LIKE $2 AND attribute NOT LIKE '%.reactions'
        """,
        run_id,
        f"{family}.%",
    )
    prefix_len = len(family) + 1  # "complaint." / "request."
    return [
        ThemeItem(
            claim_id=row["id"],
            slug=row["attribute"][prefix_len:],
            quote=row["quote"],
            source_id=row["source_id"],
            grade=row["grade"],
            confidence=float(row["confidence"]),
        )
        for row in rows
    ]


def _promotion_for(cluster: ThemeCluster) -> PromotionResult:
    return evaluate_community_theme(
        claim_ids=list(cluster.claim_ids), thread_ids=[str(s) for s in cluster.source_ids]
    )


def _draft_from_cluster(
    kind: FindingKind, cluster: ThemeCluster, promotion: PromotionResult, statement: str
) -> FindingDraft:
    return FindingDraft(
        kind=kind,
        statement=statement,
        claim_ids=cluster.claim_ids,
        support_count=promotion.support_count,
        confidence=_mean_confidence(cluster.confidences),
        theme=cluster.label,
        distinct_threads=promotion.distinct_threads,
        best_grade=min(cluster.grades, key=lambda g: _GRADE_RANK[g]),
    )


async def build_pain_point_findings(
    pool: asyncpg.Pool, run_id: str, *, embed_ctx: EmbedContext
) -> list[FindingDraft]:
    items = await _fetch_theme_items(pool, run_id, family="complaint")
    clusters = await embed_and_cluster(items, embed_ctx=embed_ctx)

    drafts = []
    for cluster in clusters:
        promotion = _promotion_for(cluster)
        if not promotion.eligible:
            continue
        statement = (
            f"{promotion.support_count} users across {promotion.distinct_threads} threads "
            f"report {cluster.label}"
        )
        drafts.append(_draft_from_cluster(FindingKind.PAIN_POINT, cluster, promotion, statement))
    return drafts


async def _shipped_feature_slugs(pool: asyncpg.Pool, run_id: str) -> set[str]:
    rows = await pool.fetch(
        """
        SELECT DISTINCT split_part(attribute, '.', 2) AS slug
          FROM claims
         WHERE run_id = $1 AND superseded_by IS NULL
           AND attribute LIKE 'feature.%.present' AND value_text = 'true'
        """,
        run_id,
    )
    return {row["slug"] for row in rows}


async def build_feature_gap_findings(
    pool: asyncpg.Pool, run_id: str, *, embed_ctx: EmbedContext, reviewed_competitors: int
) -> list[FindingDraft]:
    items = await _fetch_theme_items(pool, run_id, family="request")
    clusters = await embed_and_cluster(items, embed_ctx=embed_ctx)
    shipped = await _shipped_feature_slugs(pool, run_id)

    drafts = []
    for cluster in clusters:
        if cluster.label in shipped:
            continue  # already shipped somewhere among reviewed competitors — not a gap
        promotion = _promotion_for(cluster)
        if not promotion.eligible:
            continue
        statement = (
            f"{promotion.support_count} users across {promotion.distinct_threads} threads "
            f"request {cluster.label}; not found among {reviewed_competitors} reviewed "
            "competitors"
        )
        drafts.append(_draft_from_cluster(FindingKind.FEATURE_GAP, cluster, promotion, statement))
    return drafts


# ---------------------------------------------------------------------------
# pricing_observation — pricing.entry_usd_month claims across entities
# ---------------------------------------------------------------------------


async def build_pricing_observation_finding(pool: asyncpg.Pool, run_id: str) -> FindingDraft | None:
    rows = await pool.fetch(
        """
        SELECT id, entity_id, value_num, grade, confidence
          FROM claims
         WHERE run_id = $1 AND superseded_by IS NULL AND attribute = 'pricing.entry_usd_month'
        """,
        run_id,
    )
    distinct_entities = {row["entity_id"] for row in rows}
    if len(distinct_entities) < MIN_ENTITIES_FOR_PRICING_OBSERVATION:
        return None

    values = sorted(float(row["value_num"]) for row in rows)
    median = (
        values[len(values) // 2]
        if len(values) % 2
        else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2
    )
    statement = (
        f"Entry pricing observed across {len(distinct_entities)} competitors: "
        f"median ${median:.2f}/mo (range ${values[0]:.2f}-${values[-1]:.2f})"
    )
    return FindingDraft(
        kind=FindingKind.PRICING_OBSERVATION,
        statement=statement,
        claim_ids=tuple(row["id"] for row in rows),
        support_count=len(rows),
        confidence=_mean_confidence(tuple(float(row["confidence"]) for row in rows)),
    )


# ---------------------------------------------------------------------------
# competitor — resolved, non-synthetic entities with >=1 claim
# ---------------------------------------------------------------------------


async def build_competitor_findings(pool: asyncpg.Pool, run_id: str) -> list[FindingDraft]:
    rows = await pool.fetch(
        """
        SELECT e.id AS entity_id, e.entity_key, e.display_name,
               array_agg(c.id ORDER BY c.id) AS claim_ids
          FROM claims c
          JOIN entities e ON e.id = c.entity_id
         WHERE c.run_id = $1 AND c.superseded_by IS NULL
           AND e.entity_key NOT LIKE $2
         GROUP BY e.id, e.entity_key, e.display_name
        """,
        run_id,
        f"{_SYNTHETIC_ENTITY_PREFIX}%",
    )
    return [
        FindingDraft(
            kind=FindingKind.COMPETITOR,
            statement=f"{row['display_name']} verified with {len(row['claim_ids'])} bound claims",
            claim_ids=tuple(row["claim_ids"]),
            support_count=len(row["claim_ids"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


async def build_all_findings(
    pool: asyncpg.Pool, run_id: str, *, embed_ctx: EmbedContext
) -> list[Finding]:
    competitor_drafts = await build_competitor_findings(pool, run_id)
    pain_point_drafts = await build_pain_point_findings(pool, run_id, embed_ctx=embed_ctx)
    feature_gap_drafts = await build_feature_gap_findings(
        pool, run_id, embed_ctx=embed_ctx, reviewed_competitors=len(competitor_drafts)
    )
    pricing_draft = await build_pricing_observation_finding(pool, run_id)

    drafts = [*competitor_drafts, *pain_point_drafts, *feature_gap_drafts]
    if pricing_draft is not None:
        drafts.append(pricing_draft)

    return [await persist_finding(pool, run_id, draft) for draft in drafts]


__all__ = [
    "MIN_ENTITIES_FOR_PRICING_OBSERVATION",
    "Finding",
    "FindingDraft",
    "FindingKind",
    "build_all_findings",
    "build_competitor_findings",
    "build_feature_gap_findings",
    "build_pain_point_findings",
    "build_pricing_observation_finding",
    "persist_finding",
]
