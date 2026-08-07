"""`extract_claims(source) -> ExtractionResult` — orchestration (masterplan
§4.8, phase doc). One page per call, always: the function signature takes a
single `Source`, never a batch, so span attribution never has to ask "which
page did this quote come from" and a retry only ever re-costs one page.

`extractor_version_for` composes the prompt version (already
`{prompt_id}@{content_hash8}`, Phase 05) with the model id — resolving both
Phase 00's and Phase 05's open decision on this format here: a prompt edit
*or* a model swap must invalidate the extraction cache, because the same
page extracted by a different model is not the same claim provenance.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.extract import cache
from api.extract.metrics import DropCounts, ExtractionMetrics
from api.extract.validate import (
    ExtractedClaim,
    ExtractionResponse,
    RawExtractedClaim,
    validate_and_bind,
)
from api.llm.gateway import LLMContext, structured
from api.models.source import Source

PROMPT_ID = "extract_claims"


@dataclass
class ExtractionResult:
    claims: list[ExtractedClaim]
    metrics: ExtractionMetrics


def extractor_version_for(ctx: LLMContext) -> str:
    prompt_version = ctx.registry.get(PROMPT_ID).version
    return f"{prompt_version}-{ctx.client.model}"


async def _get_raw_claims(
    source: Source, *, ctx: LLMContext, extractor_version: str
) -> list[RawExtractedClaim]:
    assert source.content_hash is not None
    cached = await cache.get(
        ctx.pool, content_hash=source.content_hash, extractor_version=extractor_version
    )
    if cached is not None:
        return cached

    result = await structured(
        ExtractionResponse,
        PROMPT_ID,
        {"url": source.canonical_url},
        untrusted={"page_text": source.extracted_text or ""},
        ctx=ctx,
    )
    await cache.put(
        ctx.pool,
        content_hash=source.content_hash,
        extractor_version=extractor_version,
        claims=result.value.claims,
    )
    return result.value.claims


async def extract_claims(source: Source, *, ctx: LLMContext) -> ExtractionResult:
    if source.extracted_text is None or source.content_hash is None:
        raise ValueError(
            "extract_claims requires a fetched source with extracted_text and content_hash"
        )

    extractor_version = extractor_version_for(ctx)
    raw_claims = await _get_raw_claims(source, ctx=ctx, extractor_version=extractor_version)

    claims: list[ExtractedClaim] = []
    drops = DropCounts()
    for raw in raw_claims:
        outcome = validate_and_bind(
            raw, source_text=source.extracted_text, extractor_version=extractor_version
        )
        if outcome.claim is not None:
            claims.append(outcome.claim)
        else:
            assert outcome.drop_reason is not None
            drops.record(outcome.drop_reason)

    metrics = ExtractionMetrics(
        claims_returned_by_model=len(raw_claims), claims_bound=len(claims), drops=drops
    )
    return ExtractionResult(claims=claims, metrics=metrics)


__all__ = ["ExtractionResult", "extract_claims", "extractor_version_for"]
