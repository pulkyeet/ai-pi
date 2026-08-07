"""Drop-rate accounting (phase doc). Each of the four validation gates in
`api.extract.validate` produces its own drop reason, because they diagnose
different failures: a rising `quote_not_in_source` rate signals a degraded
model or a broken Phase 03 normalisation contract; `quote_ambiguous` is a
prompt-tuning signal (ask for longer quotes); `invalid_attribute` should be
near zero with a closed schema; `value_type_mismatch` is a prompt/schema
clarity issue. Tracked per run and reported in Phase 14.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DropReason(StrEnum):
    QUOTE_NOT_IN_SOURCE = "quote_not_in_source"
    QUOTE_AMBIGUOUS = "quote_ambiguous"
    INVALID_ATTRIBUTE = "invalid_attribute"
    VALUE_TYPE_MISMATCH = "value_type_mismatch"


@dataclass
class DropCounts:
    counts: dict[DropReason, int] = field(default_factory=lambda: dict.fromkeys(DropReason, 0))

    def record(self, reason: DropReason) -> None:
        self.counts[reason] += 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class ExtractionMetrics:
    claims_returned_by_model: int
    claims_bound: int
    drops: DropCounts

    @property
    def drop_rate(self) -> float:
        if self.claims_returned_by_model == 0:
            return 0.0
        return self.drops.total / self.claims_returned_by_model


__all__ = ["DropCounts", "DropReason", "ExtractionMetrics"]
