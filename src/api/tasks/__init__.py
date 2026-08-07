"""Task Handlers & End-to-End Run (Phase 10, ⭐ walking skeleton).

Wires the seven independently-proven components from Phases 02/04/06/07/09
into a pipeline that runs a real query against the real internet and
produces real, span-verified claims and artifact-verified entities in
Postgres. No report yet — that's Phase 11.

```
api.tasks.context    HandlerDeps / RunStats — every handler's shared clients
api.tasks.claims     ExtractedClaim / structured-value -> a graded, scored,
                      persisted `claims` row
api.tasks.discover   discover_competitors — the fan-out root
api.tasks.profile    profile_product (+ the fetch/extract/persist helpers
                      pricing.py and funding.py reuse)
api.tasks.pricing    extract_pricing
api.tasks.community  mine_community
api.tasks.oss        oss_profile
api.tasks.funding    find_funding
api.tasks.trends     trend_signals
api.tasks.registry   build_registry(deps) -> the wired HandlerRegistry
```

See `src/api/cli.py` for the concrete entry point:
`python -m api.cli run "<idea>"`.
"""

from __future__ import annotations

from api.tasks.registry import build_registry

__all__ = ["build_registry"]
