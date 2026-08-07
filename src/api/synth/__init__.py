"""Findings, Constrained Synthesis & Report Assembly (Phase 11, masterplan
§2/§4.9). The second milestone gate: the first report matching the
masterplan's output contract, with **100% sentence-to-claim binding**.

```
api.synth.findings   claims -> findings, templated (not generated)
                      statements, promotion applied after clustering
api.synth.cluster     pgvector-backed complaint/request theme near-
                      duplicate clustering (masterplan §11's one use)
api.synth.generate    constrained synthesis: MVP / feature gaps / risks,
                      receiving only the resolved finding set
api.synth.bind        sentence-level citation binding — the mechanical,
                      authoritative Rule 1 gate: drop, don't flag
api.synth.assemble    Report construction against the masterplan §2
                      contract; the final "refuse to return a violating
                      report" check
```

Turns a pile of graded claims (Phase 08) into a report a founder can act on,
without a single unbindable sentence surviving to the output. Like
`api.evidence`/`api.planner`, this phase has no single always-running
pipeline function to import from a caller's perspective other than
`api.synth.assemble.assemble_report` — that one function *is* the
orchestrating entry point, calling every module above in sequence.
"""

from __future__ import annotations

from api.synth import assemble, bind, cluster, findings, generate
from api.synth.assemble import RunMeta, UnboundReportError, assemble_report
from api.synth.findings import Finding, FindingKind

__all__ = [
    "Finding",
    "FindingKind",
    "RunMeta",
    "UnboundReportError",
    "assemble",
    "assemble_report",
    "bind",
    "cluster",
    "findings",
    "generate",
]
