"""Turning `ExtractedClaim`s / structured API values into persisted, graded,
confidence-scored `claims` rows — explicitly Phase 10's job per
`docs/tracker.md`'s Phase 09 Next Steps note 6 ("Wiring `grade_for`/
`confidence` into claim construction... is Phase 10's job").

Two entry points, for the two shapes of evidence the seven handlers produce:

- `persist_extracted_claims` — the LLM/span-bound path, for claims that came
  out of `api.extract.extract_claims` against a real fetched `Source`
  (`profile_product`, `extract_pricing`, `mine_community`, `find_funding`).
- `persist_structured_claim` — the synthetic-source path, for a single exact
  value read straight off a structured API (GitHub stars, license, ...) that
  never went through LLM extraction at all (`oss_profile`). The `claims`
  schema still requires `quote`/`char_start`/`char_end`/`source_id` on every
  row — masterplan Rule 1 ("every prose sentence carries a claim_id") is
  enforced structurally for *every* claim, not only ones that came from
  prose — so this constructs a short synthetic "page" of text, stores it as
  an ordinary `sources` row via `get_or_create_synthetic_source`, and binds a
  quote the caller itself wrote against that same text. This is
  deterministic and can never hit `quote_ambiguous`/`quote_not_in_source` by
  construction, as long as the caller's quote is a literal substring of the
  text it just built.

Idempotency (masterplan §4.2 "Guard 2") comes from the same mechanism every
other phase already relies on: `claims_unique_span UNIQUE (run_id, source_id,
attribute, char_start)` plus `ON CONFLICT DO NOTHING` — a handler re-run
after a lease timeout writes the same rows again for free.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import asyncpg

from api.evidence.confidence import ConfidenceInputs, age_days, confidence, distinct_domain_count
from api.extract.span import bind_span, quote_context_window
from api.extract.validate import ExtractedClaim
from api.models.claims import Grade
from api.models.source import Source
from api.retrieval import cache as source_cache

SYNTHETIC_SOURCE_TTL = timedelta(days=7)


async def _insert_claim_row(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    entity_id: int,
    attribute: str,
    value_text: str | None,
    value_num: float | None,
    unit: str | None,
    as_of: date | None,
    source_id: int,
    quote: str,
    char_start: int,
    char_end: int,
    quote_context: str,
    context_offset: int,
    grade: Grade,
    extractor_version: str,
    confidence_value: float,
    confidence_inputs: ConfidenceInputs,
) -> int | None:
    row = await pool.fetchrow(
        """
        INSERT INTO claims (
            run_id, entity_id, attribute, value_text, value_num, unit, as_of,
            source_id, quote, char_start, char_end, quote_context, context_offset,
            grade, extractor_version, confidence, confidence_inputs
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb)
        ON CONFLICT (run_id, source_id, attribute, char_start) DO NOTHING
        RETURNING id
        """,
        run_id,
        entity_id,
        attribute,
        value_text,
        value_num,
        unit,
        as_of,
        source_id,
        quote,
        char_start,
        char_end,
        quote_context,
        context_offset,
        grade.value,
        extractor_version,
        confidence_value,
        confidence_inputs.model_dump_json(),
    )
    return int(row["id"]) if row is not None else None


def _confidence_for(*, grade: Grade, source: Source, as_of: date | None) -> ConfidenceInputs:
    return ConfidenceInputs(
        best_grade=grade,
        n_distinct_domains=distinct_domain_count([source.canonical_url]),
        age_days=age_days(as_of=as_of, fetched_at=source.fetched_at),
        contradicted=False,
    )


async def persist_extracted_claims(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    entity_id: int,
    source: Source,
    claims: list[ExtractedClaim],
    grade: Grade,
) -> list[int]:
    """Grade and score every already span-bound `claims`, one `INSERT` each.
    A caller is expected to have already run `api.extract.extract_claims`
    against `source` — this function does no extraction of its own."""
    assert source.id is not None
    persisted: list[int] = []
    for claim in claims:
        inputs = _confidence_for(grade=grade, source=source, as_of=claim.as_of)
        claim_id = await _insert_claim_row(
            pool,
            run_id=run_id,
            entity_id=entity_id,
            attribute=claim.attribute,
            value_text=claim.value_text,
            value_num=claim.value_num,
            unit=claim.unit,
            as_of=claim.as_of,
            source_id=source.id,
            quote=claim.quote,
            char_start=claim.char_start,
            char_end=claim.char_end,
            quote_context=claim.quote_context,
            context_offset=claim.context_offset,
            grade=grade,
            extractor_version=claim.extractor_version,
            confidence_value=confidence(inputs),
            confidence_inputs=inputs,
        )
        if claim_id is not None:
            persisted.append(claim_id)
    return persisted


async def get_or_create_synthetic_source(
    pool: asyncpg.Pool,
    *,
    canonical_url: str,
    root_key: str | None,
    text: str,
    retrieval_reason: str,
) -> Source:
    """A `sources` row for text this package generated itself from a
    structured API response, rather than fetched HTML — same table, same
    cache semantics (a re-run within the TTL is a cache hit), just without
    `api.retrieval.fetch_source`'s HTTP path in front of it."""
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = await source_cache.get_fresh(pool, canonical_url)
    if cached is not None and cached.content_hash == content_hash:
        return cached
    return await source_cache.upsert(
        pool,
        canonical_url=canonical_url,
        root_key=root_key,
        http_status=200,
        extracted_text=text,
        content_hash=content_hash,
        etag=None,
        last_modified=None,
        retrieval_reason=retrieval_reason,
        ttl=SYNTHETIC_SOURCE_TTL,
    )


async def persist_structured_claim(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    entity_id: int,
    source: Source,
    attribute: str,
    quote: str,
    value_text: str | None = None,
    value_num: float | None = None,
    unit: str | None = None,
    as_of: date | None = None,
    grade: Grade,
    extractor_version: str = "structured-v1",
) -> int | None:
    """`quote` must be a literal, unambiguous substring of `source.
    extracted_text` — true by construction whenever the caller built both
    from the same structured value. Returns `None` (logged by the caller, not
    raised) in the one case that would indicate a real bug: the caller's own
    quote isn't actually in the text it just wrote."""
    assert source.id is not None and source.extracted_text is not None
    span = bind_span(source.extracted_text, quote)
    if span is None:
        return None
    context, offset = quote_context_window(source.extracted_text, span)
    inputs = _confidence_for(grade=grade, source=source, as_of=as_of)
    return await _insert_claim_row(
        pool,
        run_id=run_id,
        entity_id=entity_id,
        attribute=attribute,
        value_text=value_text,
        value_num=value_num,
        unit=unit,
        as_of=as_of,
        source_id=source.id,
        quote=quote,
        char_start=span.start,
        char_end=span.end,
        quote_context=context,
        context_offset=offset,
        grade=grade,
        extractor_version=extractor_version,
        confidence_value=confidence(inputs),
        confidence_inputs=inputs,
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "SYNTHETIC_SOURCE_TTL",
    "get_or_create_synthetic_source",
    "persist_extracted_claims",
    "persist_structured_claim",
    "utcnow",
]
