"""Grading, Confidence & Contradictions (Phase 08, masterplan §4.6/§4.7).

Turns a pile of claims into graded, scored evidence, and surfaces
disagreement between sources instead of silently picking a winner. Every
module here is pure arithmetic or SQL over already-typed claims — **no
model is called anywhere in this package**, which is only possible because
the claim vocabulary is closed (Phase 00).

```
api.evidence.grade           source-type -> Grade, mechanically
api.evidence.confidence      the masterplan §4.6 formula, verbatim
api.evidence.contradictions  detection (GROUP BY) + resolution (SQL)
api.evidence.promotion       anecdote -> finding eligibility thresholds
api.evidence.coverage        planned-vs-completed coverage scoring
```

Unlike `api.resolve`, this phase has no single orchestrating entry point:
grading happens once per claim at construction time, contradiction
resolution runs once per completed run, and promotion/coverage are each
called independently by Phase 11's synthesis stage. Each submodule is
therefore a complete, independently-usable unit on its own.
"""

from __future__ import annotations

from api.evidence import confidence, contradictions, coverage, grade, promotion
from api.evidence.confidence import ConfidenceInputs
from api.evidence.contradictions import ContradictionResolution
from api.evidence.coverage import CoverageResult, TaskOutcome
from api.evidence.grade import SourceKind
from api.evidence.promotion import PromotionResult

__all__ = [
    "ConfidenceInputs",
    "ContradictionResolution",
    "CoverageResult",
    "PromotionResult",
    "SourceKind",
    "TaskOutcome",
    "confidence",
    "contradictions",
    "coverage",
    "grade",
    "promotion",
]
