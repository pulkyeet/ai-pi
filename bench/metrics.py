"""Phase 14: the masterplan §10 metrics table, as pure functions over a
`Report` (`api.models.report`) plus a `GroundTruth` (`bench.loader`) and,
for the process metrics, the raw `RunOutcome` data `bench.runner` already
has in hand. No I/O, no Postgres — every function here is unit-testable on
synthetic data, per the phase doc's own testing table ("Metrics are right
before conclusions are drawn from them").

**Two metrics are honestly-scoped proxies, not the literal masterplan
definition, and each says so in its own docstring** rather than silently
approximating:

- `sentence_binding_rate` — `Report` carries aggregate `claim_ids`/
  `addresses_finding_ids` on each finding/statement, not the per-sentence
  granularity `api.synth.bind` computes internally and never returns. This
  checks the invariant `bind.py`'s own "verified programmatically at
  assembly" guarantee is *supposed* to make structurally true (every
  non-empty generated statement carries at least one finding id, which is
  what makes its `claim_ids` non-empty) rather than re-deriving a per-
  sentence count no persisted data actually carries.
- `cache_hit_rate_summary` only reports the *source* (fetch) cache hit rate
  — the only one `api.tasks.context.RunStats` currently instruments. Search
  and extraction cache hits are not counted anywhere in the pipeline today;
  this function says so explicitly rather than fabricating a number for
  either.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from api.models.report import Report
from bench.loader import GroundTruth

# Pricing snapshots taken on different days/currencies can differ by rounding.
NUMERIC_FACT_TOLERANCE_USD = 1.0


def _competitor_domains(report: Report) -> set[str]:
    """`CompetitorEntry.entity_key` is scheme-prefixed (masterplan §4.5,
    e.g. `web:asana.com`) — ground truth is bare domains, so only `web:`
    entities are comparable at all; a `gh:`/`npm:` entity can never appear
    in `must_include`/`known_absent` by construction of this benchmark's own
    ground truth format."""
    domains = set()
    for c in report.competitors:
        if c.entity_key.startswith("web:"):
            domains.add(c.entity_key.removeprefix("web:"))
    return domains


def competitor_recall(report: Report, ground_truth: GroundTruth) -> float:
    """|found ∩ must_include| / |must_include|. `1.0` (vacuously) if
    `must_include` is empty — a thin-category query is allowed to have none."""
    if not ground_truth.must_include:
        return 1.0
    found = _competitor_domains(report)
    hits = len(found & set(ground_truth.must_include))
    return hits / len(ground_truth.must_include)


def precision_proxy(report: Report, ground_truth: GroundTruth) -> float:
    """1 - |found ∩ known_absent| / |known_absent|. `1.0` (vacuously) if
    `known_absent` is empty."""
    if not ground_truth.known_absent:
        return 1.0
    found = _competitor_domains(report)
    hits = len(found & set(ground_truth.known_absent))
    return 1.0 - hits / len(ground_truth.known_absent)


def _competitor_value(report: Report, *, domain: str, attribute: str) -> float | str | bool | None:
    for c in report.competitors:
        if c.entity_key != f"web:{domain}":
            continue
        if attribute == "pricing.entry_usd_month":
            return c.pricing.entry_usd_month
        if attribute == "pricing.model":
            return c.pricing.model
        if attribute == "pricing.free_tier":
            return c.pricing.free_tier
        return None
    return None


def _fact_matches(expected: float | str | bool, actual: float | str | bool | None) -> bool:
    if actual is None:
        return False
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(float(expected) - float(actual)) <= NUMERIC_FACT_TOLERANCE_USD
    return str(expected).strip().lower() == str(actual).strip().lower()


def fact_accuracy(report: Report, ground_truth: GroundTruth) -> float:
    """matching claims / ground-truth facts, within per-attribute tolerance
    (masterplan §10). `1.0` (vacuously) if there are no facts to check.
    Numeric facts (`pricing.entry_usd_month`) match within
    `NUMERIC_FACT_TOLERANCE_USD`; everything else (`pricing.model`,
    `pricing.free_tier`) matches exactly, case/whitespace-insensitively."""
    if not ground_truth.facts:
        return 1.0
    matches = 0
    for fact in ground_truth.facts:
        actual = _competitor_value(report, domain=fact.entity, attribute=fact.attribute)
        if _fact_matches(fact.value, actual):
            matches += 1
    return matches / len(ground_truth.facts)


def sentence_binding_rate(report: Report) -> float:
    """See module docstring — a structural proxy, not a per-sentence count.
    `1.0` (vacuously) if the report has no prose statements at all."""
    checks: list[bool] = []
    if report.mvp.statement:
        checks.append(bool(report.mvp.addresses_finding_ids))
    for risk in report.risks:
        checks.append(bool(risk.addresses_finding_ids))
    for gap in report.feature_gaps:
        checks.append(bool(gap.addresses_finding_ids))
    if not checks:
        return 1.0
    return sum(checks) / len(checks)


def contradiction_fired(report: Report) -> bool:
    """Whether the run's report carries at least one contradiction group —
    the trap query's own pass condition (masterplan §10: "must be ≥1 on the
    trap query")."""
    return len(report.contradictions) > 0


def synthesis_omitted_sections(report: Report) -> list[str]:
    """`api.synth.generate`'s own rejection signal, surfaced onto
    `Report.coverage.failed_branches` as `"mvp_synthesis"`/
    `"feature_gaps_synthesis"`/`"risks_synthesis"` entries
    (`api.synth.assemble`) — this just filters down to those."""
    return [b for b in report.coverage.failed_branches if b.endswith("_synthesis")]


def extraction_drop_breakdown(claims_dropped_by_run: list[dict[str, int]]) -> dict[str, int]:
    """Sums per-run `RunStats.claims_dropped` dicts (`quote_not_in_source`,
    `quote_ambiguous`, `invalid_attribute`, `value_type_mismatch`) across
    however many runs are being reported on together."""
    total: Counter[str] = Counter()
    for run_dropped in claims_dropped_by_run:
        total.update(run_dropped)
    return dict(total)


def planner_fallback_rate(used_fallback_by_run: list[bool]) -> float:
    """Fraction of runs where Stage 1 planning fell back to the
    deterministic default plan (`api.planner.fallback`) instead of a real
    LLM-produced DAG. `0.0` if there are no runs."""
    if not used_fallback_by_run:
        return 0.0
    return sum(used_fallback_by_run) / len(used_fallback_by_run)


def synthesis_rejection_rate(reports: list[Report]) -> float:
    """Fraction of the three synthesis sections (mvp/feature_gaps/risks),
    across every run given, that were rejected and omitted. `0.0` if there
    are no runs — not vacuously `1.0`, since "no data" isn't "all rejected"."""
    if not reports:
        return 0.0
    omitted = sum(len(synthesis_omitted_sections(r)) for r in reports)
    return omitted / (3 * len(reports))


@dataclass(frozen=True)
class LatencyCostSummary:
    mean: float
    p50: float
    p95: float


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — no numpy dependency for ten data points."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(round(pct * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[idx]


def summarize(values: list[float]) -> LatencyCostSummary:
    if not values:
        return LatencyCostSummary(mean=0.0, p50=0.0, p95=0.0)
    return LatencyCostSummary(
        mean=sum(values) / len(values),
        p50=_percentile(values, 0.50),
        p95=_percentile(values, 0.95),
    )


@dataclass(frozen=True)
class CostSummary:
    total: LatencyCostSummary
    llm: LatencyCostSummary
    search: LatencyCostSummary


def cost_summary(
    *, total_usd: list[float], llm_usd: list[float], search_usd: list[float]
) -> CostSummary:
    return CostSummary(
        total=summarize(total_usd), llm=summarize(llm_usd), search=summarize(search_usd)
    )


def latency_summary(duration_s: list[float]) -> LatencyCostSummary:
    return summarize(duration_s)


@dataclass(frozen=True)
class CacheHitRateSummary:
    source_mean: float
    # Not instrumented anywhere in the pipeline today (see module
    # docstring) — `None` is an honest "not measured", never a fabricated 0.
    search_mean: float | None = None
    extraction_mean: float | None = None


def cache_hit_rate_summary(source_cache_hit_rates: list[float]) -> CacheHitRateSummary:
    n = len(source_cache_hit_rates)
    mean = sum(source_cache_hit_rates) / n if n else 0.0
    return CacheHitRateSummary(source_mean=mean)


@dataclass(frozen=True)
class CoverageSummary:
    mean: float
    failed_branch_counts: dict[str, int] = field(default_factory=dict)


def coverage_summary(
    coverage_scores: list[float], failed_branches_by_run: list[list[str]]
) -> CoverageSummary:
    mean = sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0.0
    tally: Counter[str] = Counter()
    for branches in failed_branches_by_run:
        tally.update(branches)
    return CoverageSummary(mean=mean, failed_branch_counts=dict(tally))


__all__ = [
    "NUMERIC_FACT_TOLERANCE_USD",
    "CacheHitRateSummary",
    "CostSummary",
    "CoverageSummary",
    "LatencyCostSummary",
    "cache_hit_rate_summary",
    "competitor_recall",
    "contradiction_fired",
    "cost_summary",
    "coverage_summary",
    "extraction_drop_breakdown",
    "fact_accuracy",
    "latency_summary",
    "planner_fallback_rate",
    "precision_proxy",
    "sentence_binding_rate",
    "summarize",
    "synthesis_omitted_sections",
    "synthesis_rejection_rate",
]
