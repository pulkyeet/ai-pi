"""Maturity tier derivation (masterplan §4.5, phase doc's Design section).

An ordered decision list, evaluated first-match, exactly as the phase doc
specifies it — never a model's judgement call. Every assignment records
which rule fired (`MaturityAssignment.rule`) so a report can state *why*
something is `hobby` rather than just asserting it. Thresholds are
explicitly first-pass guesses, tunable in Phase 14 against the benchmark
set — see the module-level constants below, all named so that tuning is a
one-line change.

```
abandoned  <- last commit > 18 months AND no other liveness signal
funded     <- any A/B-grade funding claim OR company.stage in {seed, series-*}
established <- domain age > 3y AND (installs > 100k OR downloads > 100k/mo OR stars > 5k)
hobby      <- PaaS subdomain OR (stars < 100 AND downloads < 1k/mo), if known
indie      <- otherwise
```

`indie` is also where an unknown-signal case lands, per the phase doc's own
rule: never guess a tier, default to `indie` and set `insufficient_signal`,
which is meant to feed the report's `coverage` accounting (Phase 11/14) —
this module only records the flag; it does not compute coverage itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from api.models.entity import Maturity

ABANDONED_MONTHS = 18
ESTABLISHED_DOMAIN_YEARS = 3.0
ESTABLISHED_INSTALLS = 100_000
ESTABLISHED_DOWNLOADS_PER_MONTH = 100_000
ESTABLISHED_STARS = 5_000
HOBBY_STARS = 100
HOBBY_DOWNLOADS_PER_MONTH = 1_000
_DAYS_PER_MONTH = 30.44
_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class MaturitySignals:
    """One entity's worth of the phase doc's Signal table. `None` means
    "not observed", distinct from a real zero — see `derive_maturity`'s
    "known low scale" handling, which must not treat "unknown" as "low"."""

    domain_age_days: int | None = None
    last_commit_at: datetime | None = None
    stars: int | None = None
    star_velocity_90d: float | None = None
    has_funding_claim: bool = False
    company_stage: str | None = None
    store_installs: int | None = None
    downloads_per_month: int | None = None
    is_paas_subdomain: bool = False


@dataclass(frozen=True)
class MaturityAssignment:
    tier: Maturity
    rule: str
    insufficient_signal: bool = False


def _is_funded_stage(stage: str | None) -> bool:
    return stage is not None and (stage == "seed" or stage.startswith("series-"))


def _has_any_signal(signals: MaturitySignals) -> bool:
    return any(
        (
            signals.domain_age_days is not None,
            signals.last_commit_at is not None,
            signals.stars is not None,
            signals.star_velocity_90d is not None,
            signals.has_funding_claim,
            signals.company_stage is not None,
            signals.store_installs is not None,
            signals.downloads_per_month is not None,
            signals.is_paas_subdomain,
        )
    )


def derive_maturity(signals: MaturitySignals, *, now: datetime | None = None) -> MaturityAssignment:
    now = now or datetime.now(UTC)

    if signals.last_commit_at is not None:
        months_since_commit = (now - signals.last_commit_at).days / _DAYS_PER_MONTH
        no_other_liveness = (
            (signals.stars or 0) == 0
            and (signals.store_installs or 0) == 0
            and (signals.downloads_per_month or 0) == 0
        )
        if months_since_commit > ABANDONED_MONTHS and no_other_liveness:
            return MaturityAssignment(Maturity.ABANDONED, "abandoned:stale_commit_no_liveness")

    if signals.has_funding_claim or _is_funded_stage(signals.company_stage):
        return MaturityAssignment(Maturity.FUNDED, "funded:funding_signal")

    domain_age_years = (
        signals.domain_age_days / _DAYS_PER_YEAR if signals.domain_age_days is not None else None
    )
    if domain_age_years is not None and domain_age_years > ESTABLISHED_DOMAIN_YEARS:
        scale_signal = (
            (signals.store_installs or 0) > ESTABLISHED_INSTALLS
            or (signals.downloads_per_month or 0) > ESTABLISHED_DOWNLOADS_PER_MONTH
            or (signals.stars or 0) > ESTABLISHED_STARS
        )
        if scale_signal:
            return MaturityAssignment(Maturity.ESTABLISHED, "established:domain_age_and_scale")

    known_low_scale = (
        (signals.stars is not None or signals.downloads_per_month is not None)
        and (signals.stars or 0) < HOBBY_STARS
        and (signals.downloads_per_month or 0) < HOBBY_DOWNLOADS_PER_MONTH
    )
    if signals.is_paas_subdomain:
        return MaturityAssignment(Maturity.HOBBY, "hobby:paas_subdomain")
    if known_low_scale:
        return MaturityAssignment(Maturity.HOBBY, "hobby:low_scale")

    if not _has_any_signal(signals):
        return MaturityAssignment(
            Maturity.INDIE, "indie:insufficient_signal", insufficient_signal=True
        )

    return MaturityAssignment(Maturity.INDIE, "indie:default")


__all__ = ["MaturityAssignment", "MaturitySignals", "derive_maturity"]
