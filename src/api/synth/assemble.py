"""Report assembly (masterplan §2, phase doc's Design section) — the
acceptance target for Phase 11: constructs the `Report` contract from
findings, claims, entities, and coverage, computes `freshness`, and refuses
to persist or return anything that fails a final Rule 1 check.

**Two frozen Phase 00 leaf fields, widened here, logged rather than worked
around silently** (see `api.models.report` for the full reasoning at each
field): `CompetitorEntry.maturity` is `Maturity | None` — Phase 07's
maturity classifier returns no verdict (`insufficient_signal`) for
essentially every real `web:` entity today (docs/tracker.md's Phase 10
entry), and `Maturity` has no "unknown" member to fabricate instead.
`ContradictionValue.v` is `float | str` — `api.evidence.contradictions`
(Phase 08) detects contradictions on non-numeric attributes too
(`pricing.model`, `company.stage`, ...), which a numeric-only field would
silently drop half of.

**Two required (non-Optional) sections, `mvp` and `pricing_landscape`, that
the phase doc's "omit the section" instruction can't literally apply to** —
`Report.feature_gaps`/`risks`/`pain_points`/`contradictions`/`competitors`
are lists, where "omitted" is naturally `[]`; `mvp: MVP` and
`pricing_landscape: PricingLandscape` are singular and required by the
frozen contract. Resolved the same way `Coverage.score` already handles a
totally-failed run: an honest, empty degenerate value —
`MVP(statement="", addresses_finding_ids=[])`,
`PricingLandscape(median_entry_usd_month=0.0, spread=(0.0, 0.0),
claim_ids=[])` — rather than a fabricated non-empty one. Omitted synthesis
sections are still recorded, in `coverage.failed_branches`
(`"mvp_synthesis"`, `"feature_gaps_synthesis"`, `"risks_synthesis"`), so the
omission is visible on the report rather than silently absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import asyncpg

from api.evidence.coverage import CoverageResult
from api.llm.embed import EmbedContext
from api.llm.gateway import LLMContext
from api.models.brief import ResearchBrief
from api.models.claims import Grade
from api.models.entity import Maturity
from api.models.report import (
    MVP,
    CompetitorEntry,
    CompetitorPricing,
    Contradiction,
    ContradictionValue,
    Coverage,
    FeatureGap,
    Freshness,
    Meta,
    PainPoint,
    PricingLandscape,
    Report,
    Risk,
)
from api.synth import bind, generate
from api.synth.findings import Finding, FindingKind, build_all_findings


class UnboundReportError(Exception):
    """Raised if assembly produced a report violating Rule 1 — should be
    unreachable given `api.synth.bind`'s own guarantees and the `findings`
    table's `findings_must_cite` CHECK, but assembly asserts rather than
    trusts (phase doc: "verified programmatically at assembly... not a
    test")."""


@dataclass(frozen=True)
class RunMeta:
    cost_usd: float
    duration_s: float
    sources_fetched: int
    cache_hit_rate: float


def _effective_date(as_of: date | None, fetched_at: datetime | None) -> date:
    """`as_of` (the claim's own stated date) wins when present; otherwise
    the fetching source's `fetched_at`; otherwise today — the same
    preference order as `api.evidence.confidence.age_days`, applied here to
    fields (`Freshness.oldest`, `ContradictionValue.as_of`) that are
    required non-optional dates on the frozen contract, so some date must
    always be produced."""
    if as_of is not None:
        return as_of
    if fetched_at is not None:
        return fetched_at.date()
    return datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# pain_points — pure, from already-built findings (no DB access)
# ---------------------------------------------------------------------------


def build_pain_points(findings: list[Finding]) -> list[PainPoint]:
    pain_points = []
    for f in findings:
        if f.kind is not FindingKind.PAIN_POINT:
            continue
        assert f.theme is not None and f.distinct_threads is not None and f.best_grade is not None
        pain_points.append(
            PainPoint(
                theme=f.theme,
                support_count=f.support_count or 0,
                distinct_threads=f.distinct_threads,
                grade=Grade(f.best_grade),
                confidence=f.confidence or 0.0,
                claim_ids=f.claim_ids,
            )
        )
    return pain_points


# ---------------------------------------------------------------------------
# pricing_landscape — every pricing.entry_usd_month claim, regardless of the
# >=2-entity threshold that gates the pricing_observation *finding* (this is
# a computed section, not prose citing findings — Rule 1 doesn't gate it).
# ---------------------------------------------------------------------------


async def build_pricing_landscape(pool: asyncpg.Pool, run_id: str) -> PricingLandscape:
    rows = await pool.fetch(
        "SELECT id, value_num FROM claims WHERE run_id = $1 AND superseded_by IS NULL "
        "AND attribute = 'pricing.entry_usd_month'",
        run_id,
    )
    if not rows:
        return PricingLandscape(median_entry_usd_month=0.0, spread=(0.0, 0.0), claim_ids=[])

    values = sorted(float(r["value_num"]) for r in rows)
    n = len(values)
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    return PricingLandscape(
        median_entry_usd_month=median,
        spread=(values[0], values[-1]),
        claim_ids=[r["id"] for r in rows],
    )


# ---------------------------------------------------------------------------
# competitors — resolved, non-synthetic entities with a complete pricing
# triple (model + entry_usd_month + free_tier). An entity missing any one of
# the three is excluded, not padded with a fabricated value — CompetitorPricing
# has no optional fields, and Rule 1 rules out inventing one.
#
# Exception (Phase 14 finding): `pricing.model == "free"` has no honest
# `entry_usd_month` to report — a permanently free product's own pricing
# page never states one, so requiring that claim made this whole category of
# entity structurally unable to appear in `report.competitors`, no matter how
# well it was discovered and verified. `entry_usd_month` defaults to 0.0 in
# that one case instead — not fabricated, just the only truthful number for
# "there is no paid tier."
# ---------------------------------------------------------------------------


def _positioning(
    *, maturity: Maturity | None, platforms: list[str], pricing_model: str | None
) -> str:
    tier = maturity.value.capitalize() if maturity is not None else "Unclassified"
    parts = [f"{tier} competitor"]
    if platforms:
        parts.append("on " + ", ".join(sorted(set(platforms))))
    if pricing_model:
        parts.append(f"with {pricing_model}-based pricing")
    return " ".join(parts) + "."


async def build_competitors(pool: asyncpg.Pool, run_id: str) -> list[CompetitorEntry]:
    entities = await pool.fetch(
        """
        SELECT DISTINCT e.id, e.entity_key, e.display_name, e.maturity
          FROM claims c JOIN entities e ON e.id = c.entity_id
         WHERE c.run_id = $1 AND c.superseded_by IS NULL AND e.entity_key NOT LIKE 'category:%'
        """,
        run_id,
    )
    pricing_rows = await pool.fetch(
        """
        SELECT id, entity_id, attribute, value_text, value_num
          FROM claims
         WHERE run_id = $1 AND superseded_by IS NULL
           AND attribute IN ('pricing.model', 'pricing.entry_usd_month', 'pricing.free_tier')
        """,
        run_id,
    )
    pricing_by_entity: dict[int, dict[str, asyncpg.Record]] = {}
    for row in pricing_rows:
        pricing_by_entity.setdefault(row["entity_id"], {})[row["attribute"]] = row

    platform_rows = await pool.fetch(
        "SELECT id, entity_id, value_text FROM claims "
        "WHERE run_id = $1 AND superseded_by IS NULL AND attribute = 'product.platforms'",
        run_id,
    )
    platforms_by_entity: dict[int, list[asyncpg.Record]] = {}
    for row in platform_rows:
        platforms_by_entity.setdefault(row["entity_id"], []).append(row)

    entries = []
    for e in entities:
        pricing = pricing_by_entity.get(e["id"], {})
        model_row = pricing.get("pricing.model")
        entry_row = pricing.get("pricing.entry_usd_month")
        free_tier_row = pricing.get("pricing.free_tier")
        is_permanently_free = model_row is not None and model_row["value_text"] == "free"
        if model_row is None or free_tier_row is None:
            continue
        if entry_row is None and not is_permanently_free:
            continue

        platform_claims = platforms_by_entity.get(e["id"], [])
        platform_values = [r["value_text"] for r in platform_claims if r["value_text"]]
        maturity = Maturity(e["maturity"]) if e["maturity"] is not None else None

        claim_ids = [model_row["id"], free_tier_row["id"]]
        if entry_row is not None:
            claim_ids.append(entry_row["id"])
        claim_ids.extend(r["id"] for r in platform_claims)

        entries.append(
            CompetitorEntry(
                entity_key=e["entity_key"],
                display_name=e["display_name"],
                maturity=maturity,
                positioning=_positioning(
                    maturity=maturity,
                    platforms=platform_values,
                    pricing_model=model_row["value_text"],
                ),
                pricing=CompetitorPricing(
                    model=model_row["value_text"],
                    entry_usd_month=float(entry_row["value_num"]) if entry_row is not None else 0.0,
                    free_tier=free_tier_row["value_text"] == "true",
                ),
                claim_ids=claim_ids,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# contradictions — every claim `api.evidence.contradictions` already
# resolved this run (superseded_by set on the loser), grouped back into one
# entry per (entity, attribute, winner).
# ---------------------------------------------------------------------------


async def build_contradictions(pool: asyncpg.Pool, run_id: str) -> list[Contradiction]:
    rows = await pool.fetch(
        """
        SELECT c.id, c.entity_id, c.attribute, c.value_num, c.value_text, c.grade,
               c.as_of, c.superseded_by, s.fetched_at, e.entity_key
          FROM claims c
          JOIN entities e ON e.id = c.entity_id
          JOIN sources s ON s.id = c.source_id
         WHERE c.run_id = $1
           AND (c.superseded_by IS NOT NULL
                OR c.id IN (SELECT DISTINCT superseded_by FROM claims
                             WHERE run_id = $1 AND superseded_by IS NOT NULL))
        """,
        run_id,
    )

    groups: dict[tuple[int, str, int], list[asyncpg.Record]] = {}
    for row in rows:
        winner_id = row["superseded_by"] if row["superseded_by"] is not None else row["id"]
        groups.setdefault((row["entity_id"], row["attribute"], winner_id), []).append(row)

    contradictions = []
    for (_entity_id, attribute, _winner_id), group_rows in groups.items():
        values = [
            ContradictionValue(
                v=(
                    float(r["value_num"]) if r["value_num"] is not None else (r["value_text"] or "")
                ),
                src=r["id"],
                grade=Grade(r["grade"]),
                as_of=_effective_date(r["as_of"], r["fetched_at"]),
            )
            for r in group_rows
        ]
        contradictions.append(
            Contradiction(
                entity_key=group_rows[0]["entity_key"], attribute=attribute, values=values
            )
        )
    return contradictions


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


async def build_freshness(pool: asyncpg.Pool, run_id: str) -> Freshness:
    rows = await pool.fetch(
        """
        SELECT c.as_of, s.fetched_at
          FROM claims c JOIN sources s ON s.id = c.source_id
         WHERE c.run_id = $1 AND c.superseded_by IS NULL
        """,
        run_id,
    )
    today = datetime.now(UTC).date()
    dates = [_effective_date(r["as_of"], r["fetched_at"]) for r in rows]
    if not dates:
        return Freshness(median_source_age_days=0, oldest=today)

    ages = sorted((today - d).days for d in dates)
    n = len(ages)
    median_age = ages[n // 2] if n % 2 else round((ages[n // 2 - 1] + ages[n // 2]) / 2)
    return Freshness(median_source_age_days=median_age, oldest=min(dates))


# ---------------------------------------------------------------------------
# constrained synthesis -> bound MVP / feature gaps / risks
# ---------------------------------------------------------------------------


def _bind_mvp(response: generate.MVPResponse | None, findings_by_id: dict[int, Finding]) -> MVP:
    if response is not None:
        bound = bind.bind_statement(response.statement, findings_by_id=findings_by_id)
        if bound is not None:
            return MVP(
                statement=bound.statement,
                addresses_finding_ids=list(bound.addresses_finding_ids),
            )
    return MVP(statement="", addresses_finding_ids=[])


def _bind_feature_gaps(
    response: generate.FeatureGapsResponse | None, findings_by_id: dict[int, Finding]
) -> list[FeatureGap]:
    if response is None:
        return []
    texts = [g.statement for g in response.gaps]
    bound = bind.bind_many(texts, findings_by_id=findings_by_id)
    return [
        FeatureGap(statement=b.statement, addresses_finding_ids=list(b.addresses_finding_ids))
        for b in bound
    ]


def _bind_risks(
    response: generate.RisksResponse | None, findings_by_id: dict[int, Finding]
) -> list[Risk]:
    if response is None:
        return []
    texts = [r.statement for r in response.risks]
    bound = bind.bind_many(texts, findings_by_id=findings_by_id)
    return [
        Risk(statement=b.statement, addresses_finding_ids=list(b.addresses_finding_ids))
        for b in bound
    ]


# ---------------------------------------------------------------------------
# the final gate
# ---------------------------------------------------------------------------


def _assert_binding(report: Report) -> None:
    if report.mvp.statement and not report.mvp.addresses_finding_ids:
        raise UnboundReportError("mvp has prose text but no addresses_finding_ids")
    for risk in report.risks:
        if not risk.addresses_finding_ids:
            raise UnboundReportError(f"risk has no addresses_finding_ids: {risk.statement!r}")
    for gap in report.feature_gaps:
        if not gap.addresses_finding_ids:
            raise UnboundReportError(f"feature_gap has no addresses_finding_ids: {gap.statement!r}")
    for pp in report.pain_points:
        if not pp.claim_ids:
            raise UnboundReportError(f"pain_point has no claim_ids: {pp.theme!r}")
    for comp in report.competitors:
        if not comp.claim_ids:
            raise UnboundReportError(f"competitor has no claim_ids: {comp.display_name!r}")


async def persist_report(pool: asyncpg.Pool, run_id: str, report: Report) -> None:
    await pool.execute(
        """
        INSERT INTO reports (run_id, payload) VALUES ($1, $2::jsonb)
        ON CONFLICT (run_id) DO UPDATE SET payload = EXCLUDED.payload
        """,
        run_id,
        report.model_dump_json(),
    )


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


async def assemble_report(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    query: str,
    brief: ResearchBrief,
    llm_ctx: LLMContext,
    embed_ctx: EmbedContext,
    coverage: CoverageResult,
    meta: RunMeta,
) -> Report:
    findings = await build_all_findings(pool, run_id, embed_ctx=embed_ctx)
    findings_by_id = {f.id: f for f in findings}

    competitors = await build_competitors(pool, run_id)
    pricing_landscape = await build_pricing_landscape(pool, run_id)
    pain_points = build_pain_points(findings)
    contradictions = await build_contradictions(pool, run_id)
    freshness = await build_freshness(pool, run_id)

    synthesis = await generate.synthesise(findings, ctx=llm_ctx)
    mvp = _bind_mvp(synthesis.mvp, findings_by_id)
    feature_gaps = _bind_feature_gaps(synthesis.feature_gaps, findings_by_id)
    risks = _bind_risks(synthesis.risks, findings_by_id)

    failed_branches = [*coverage.failed_branches, *synthesis.omitted_sections]

    report = Report(
        run_id=run_id,
        query=query,
        brief=brief,
        competitors=competitors,
        pricing_landscape=pricing_landscape,
        pain_points=pain_points,
        feature_gaps=feature_gaps,
        contradictions=contradictions,
        mvp=mvp,
        risks=risks,
        coverage=Coverage(score=coverage.score, failed_branches=failed_branches),
        freshness=freshness,
        meta=Meta(
            cost_usd=meta.cost_usd,
            duration_s=meta.duration_s,
            sources_fetched=meta.sources_fetched,
            cache_hit_rate=meta.cache_hit_rate,
        ),
    )

    _assert_binding(report)
    await persist_report(pool, run_id, report)
    return report


__all__ = [
    "RunMeta",
    "UnboundReportError",
    "assemble_report",
    "build_competitors",
    "build_contradictions",
    "build_freshness",
    "build_pain_points",
    "build_pricing_landscape",
    "persist_report",
]
