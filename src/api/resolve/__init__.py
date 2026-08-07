"""Entity Resolution & Identity (Phase 07, masterplan §4.5).

The package's concrete output: `resolve_entity(ctx, evidence) -> Entity |
None`, producing canonical entity keys, merged aliases, and a derived
maturity tier — with the `.fly.dev` collapse case proven impossible (see
`tests/integration/test_resolve_store.py`'s named signature test).

```
api.resolve.entity_key   scheme-prefixed key derivation, PSL handling
api.resolve.verify       artifact verification (masterplan Rule 2)
api.resolve.alias        merge-trigger detection + order-independent graph
api.resolve.maturity     explainable maturity-tier decision list
api.resolve.store        idempotent, concurrency-safe Postgres persistence
api.resolve.types        EntityEvidence / VerificationContext shared types
```
"""

from __future__ import annotations

from dataclasses import replace

import structlog

from api.models.entity import EntityScheme, Maturity
from api.resolve import alias, entity_key, maturity, store, verify
from api.resolve.maturity import MaturityAssignment, MaturitySignals
from api.resolve.store import Entity
from api.resolve.types import EntityEvidence, VerificationContext
from api.resolve.verify import VerificationResult

logger = structlog.get_logger()


async def resolve_entity(ctx: VerificationContext, evidence: EntityEvidence) -> Entity | None:
    """Derive a canonical key, verify the candidate has a real public
    artifact (masterplan Rule 2 — returns `None`, discarded, if not),
    classify its maturity, persist it, and merge any of the three
    alias-merge triggers this candidate's evidence satisfies.
    """
    key = entity_key.derive_key(evidence.scheme, evidence.raw_value)
    result = await verify.verify_entity(ctx, key)
    if not result.verified:
        logger.info("resolve.discarded", entity_key=str(key), reason=result.reason)
        return None

    signals = evidence.signals
    if key.scheme is EntityScheme.WEB:
        signals = replace(signals, is_paas_subdomain=entity_key.is_paas_host(key.value))
    assignment = maturity.derive_maturity(signals)

    meta = {
        "maturity_rule": assignment.rule,
        "insufficient_signal": assignment.insufficient_signal,
        "verification_grade": result.grade,
    }
    entity = await store.upsert_entity(
        ctx.pool,
        entity_key=str(key),
        display_name=evidence.display_name,
        maturity=assignment.tier,
        meta=meta,
    )

    # Opportunistically fold in homepage/repository facts `verify.py`
    # observed for free, so a caller doesn't have to already know them for
    # the gh_homepage / package_repository triggers to fire.
    merged_evidence = replace(
        evidence,
        homepage_url=evidence.homepage_url or result.homepage_url,
        repository_url=evidence.repository_url or result.repository_url,
    )
    for edge in alias.detect_alias_edges(key, merged_evidence):
        canonical, other = alias.canonical_pair(edge.a, edge.b)
        merged_id = await store.merge_alias(
            ctx.pool, canonical_key=str(canonical), alias_key=str(other)
        )
        if merged_id is not None and canonical != key:
            refreshed = await store.get_entity(ctx.pool, merged_id)
            if refreshed is not None:
                entity = refreshed

    return entity


__all__ = [
    "Entity",
    "EntityEvidence",
    "EntityScheme",
    "Maturity",
    "MaturityAssignment",
    "MaturitySignals",
    "VerificationContext",
    "VerificationResult",
    "alias",
    "entity_key",
    "maturity",
    "resolve_entity",
    "store",
    "verify",
]
