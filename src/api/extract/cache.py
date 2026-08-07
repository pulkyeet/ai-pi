"""The extraction cache: `content_hash + extractor_version`, permanent
(masterplan §9, phase doc). The most valuable cache in the system — the same
page under the same extractor version costs nothing forever, which is what
makes benchmark re-runs and Phase 14 CI replay free.

Permanence is safe because both key components are content-addressed: a
changed page changes `content_hash`; a changed prompt or model changes
`extractor_version` (`api.extract.extractor.extractor_version_for`). A stale
entry is therefore unreachable rather than wrong.

Stores the model's **raw** claims (post schema validation, pre vocabulary/
value-type/span validation) rather than the final `ExtractedClaim`s: a cache
hit still re-runs the full validation pipeline against the *current* source
text on read, so a Phase 03 normalisation change surfaces as a fresh
drop-rate signal instead of silently serving stale spans (phase doc:
"Cached claims are re-bound against current source text on read rather than
trusting stored offsets").

Distinct from Phase 05's `api.llm.cache` — that one is a transport-level
cache keyed on the exact rendered prompt; this one is a domain-level cache
keyed on page content. Both exist; they are not interchangeable.
"""

from __future__ import annotations

import json

import asyncpg

from api.extract.validate import RawExtractedClaim


async def get(
    pool: asyncpg.Pool, *, content_hash: str, extractor_version: str
) -> list[RawExtractedClaim] | None:
    row = await pool.fetchrow(
        "SELECT claims FROM extraction_cache WHERE content_hash = $1 AND extractor_version = $2",
        content_hash,
        extractor_version,
    )
    if row is None:
        return None
    payload = row["claims"]
    items = json.loads(payload) if isinstance(payload, str) else payload
    return [RawExtractedClaim.model_validate(item) for item in items]


async def put(
    pool: asyncpg.Pool,
    *,
    content_hash: str,
    extractor_version: str,
    claims: list[RawExtractedClaim],
) -> None:
    await pool.execute(
        """
        INSERT INTO extraction_cache (content_hash, extractor_version, claims, created_at)
        VALUES ($1, $2, $3::jsonb, now())
        ON CONFLICT (content_hash, extractor_version) DO NOTHING
        """,
        content_hash,
        extractor_version,
        json.dumps([c.model_dump(mode="json") for c in claims]),
    )


__all__ = ["get", "put"]
