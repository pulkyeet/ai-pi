"""`extract_claims` end to end: the fixture corpus (offline, no LLM/DB),
plus the extraction cache and gateway wiring against real Postgres and a
scripted OpenRouter transport (masterplan §4.8/§9, phase doc).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import httpx
import pytest
from _db import insert_run
from _http import ScriptedTransport, make_client

from api.extract import cache as extraction_cache
from api.extract.extractor import extract_claims, extractor_version_for
from api.extract.metrics import DropReason
from api.extract.validate import RawExtractedClaim, validate_and_bind
from api.llm.gateway import build_context
from api.models.source import Source

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "extraction"
CHAT_PATH = "/api/v1/chat/completions"

_STEMS = sorted({p.stem for p in FIXTURES_DIR.glob("*.txt")})


def _load_fixture(stem: str) -> tuple[str, list[RawExtractedClaim], dict[str, object]]:
    source_text = (FIXTURES_DIR / f"{stem}.txt").read_text(encoding="utf-8")
    raw_payload = json.loads((FIXTURES_DIR / f"{stem}.llm.json").read_text(encoding="utf-8"))
    raw_claims = [RawExtractedClaim.model_validate(c) for c in raw_payload["claims"]]
    expected = json.loads((FIXTURES_DIR / f"{stem}.expected.json").read_text(encoding="utf-8"))
    return source_text, raw_claims, expected


def _run_pipeline(
    source_text: str, raw_claims: list[RawExtractedClaim], *, extractor_version: str = "test@v1"
) -> tuple[list[dict[str, object]], dict[str, int]]:
    survived: list[dict[str, object]] = []
    drop_counts: dict[str, int] = {}
    for raw in raw_claims:
        outcome = validate_and_bind(
            raw, source_text=source_text, extractor_version=extractor_version
        )
        if outcome.claim is not None:
            c = outcome.claim
            survived.append(
                {
                    "attribute": c.attribute,
                    "value_text": c.value_text,
                    "value_num": c.value_num,
                    "quote": c.quote,
                }
            )
            assert source_text[c.char_start : c.char_end] == c.quote
            assert c.quote_context[c.context_offset : c.context_offset + len(c.quote)] == c.quote
        else:
            assert outcome.drop_reason is not None
            reason = outcome.drop_reason.value
            drop_counts[reason] = drop_counts.get(reason, 0) + 1
    return survived, drop_counts


# ---------------------------------------------------------------------------
# The fixture corpus — offline, deterministic, no LLM/DB involved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", _STEMS)
def test_fixture_corpus_matches_expected_claims_and_drops(stem: str) -> None:
    source_text, raw_claims, expected = _load_fixture(stem)
    survived, drop_counts = _run_pipeline(source_text, raw_claims)

    expected_claims = expected["claims"]
    assert isinstance(expected_claims, list)
    survived_sorted = sorted(survived, key=lambda c: (str(c["attribute"]), str(c["quote"])))
    expected_sorted = sorted(expected_claims, key=lambda c: (str(c["attribute"]), str(c["quote"])))
    assert survived_sorted == expected_sorted

    assert drop_counts == expected["drop_reason_counts"]


def test_every_surviving_claim_across_the_corpus_has_a_verified_span() -> None:
    for stem in _STEMS:
        source_text, raw_claims, _ = _load_fixture(stem)
        for raw in raw_claims:
            outcome = validate_and_bind(raw, source_text=source_text, extractor_version="v1")
            if outcome.claim is not None:
                c = outcome.claim
                assert source_text[c.char_start : c.char_end] == c.quote


def test_adversarial_ignore_instructions_yields_only_vocabulary_claims() -> None:
    """masterplan §8.3's worked example: best case for the attacker is a
    claim whose quote genuinely appears on the page — an ordinary,
    gradeable, contradictory claim, never free-text leakage."""
    source_text, raw_claims, _ = _load_fixture("adversarial_ignore_instructions")
    survived, _ = _run_pipeline(source_text, raw_claims)
    assert len(survived) == 2
    assert {c["attribute"] for c in survived} == {"pricing.entry_usd_month"}
    assert {c["value_num"] for c in survived} == {15, 0}
    for c in survived:
        quote = str(c["quote"])
        assert source_text.find(quote) != -1
        assert "IGNORE ALL PREVIOUS INSTRUCTIONS" not in quote


_VOCAB_ESCAPE_STEMS = [
    s for s in _STEMS if s.startswith(("adversarial_invented", "adversarial_fabricated"))
]


@pytest.mark.parametrize("stem", _VOCAB_ESCAPE_STEMS)
def test_adversarial_vocabulary_and_fabrication_attempts_produce_no_claims(stem: str) -> None:
    source_text, raw_claims, _ = _load_fixture(stem)
    survived, _ = _run_pipeline(source_text, raw_claims)
    assert survived == []


# ---------------------------------------------------------------------------
# Large page: no truncation-induced offset drift
# ---------------------------------------------------------------------------


def test_large_page_offsets_stay_correct() -> None:
    quote = "the_unique_pricing_sentence_near_the_end"
    padding = "x" * 250_000
    source_text = padding + quote + padding  # ~500 KB
    raw = RawExtractedClaim(attribute="pricing.model", value_text="flat", quote=quote)

    outcome = validate_and_bind(raw, source_text=source_text, extractor_version="v1")

    assert outcome.claim is not None
    assert source_text[outcome.claim.char_start : outcome.claim.char_end] == quote
    assert outcome.claim.char_start == len(padding)


# ---------------------------------------------------------------------------
# extract_claims() orchestration against real Postgres + a scripted gateway
# ---------------------------------------------------------------------------
# No module-level `skip_without_postgres` here (unlike test_llm_gateway.py):
# this file also has DB-free tests above (fixture corpus, large-page), which
# must keep running even when no Postgres is reachable. The `pg_pool`
# fixture itself already depends on `skip_without_postgres` (see
# conftest.py), so every test below that takes `pg_pool` skips correctly on
# its own.


def _source(text: str, *, content_hash: str | None = None) -> Source:
    """`extraction_cache` is keyed on `content_hash` alone and, like
    `llm_response_cache` in Phase 05, is deliberately permanent in this
    long-lived Postgres container — so a literal, repeated `text` across
    test runs would silently hit a previous run's cache row. A `uuid4`
    marker keeps every call's `content_hash` unique unless the caller
    explicitly wants a shared one (the re-bind-on-read test)."""
    url = f"https://t{uuid.uuid4().hex[:12]}.example/pricing"
    unique_text = text if content_hash is not None else f"{text} <!-- {uuid.uuid4().hex} -->"
    return Source(
        canonical_url=url,
        extracted_text=unique_text,
        content_hash=content_hash or hashlib.sha256(unique_text.encode("utf-8")).hexdigest(),
    )


def _chat_response(claims: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps({"claims": claims})}}],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


MODEL = "deepseek/deepseek-v4-flash"  # the only model with a configured cost rate (Phase 05)

_PROMPT_TEMPLATE = """---
id: extract_claims
schema: ExtractionResponse
cache_prefix_ends_after: instructions
---
## role
Test double for the real extract_claims prompt — variant {variant}.

## instructions
Extract claims from the page text below into the required schema.

## user
Page URL: {{{{url}}}}
"""


def _make_ctx(
    pg_pool,
    transport: ScriptedTransport,
    run_id: str,
    *,
    model: str = MODEL,
    prompts_dir: Path | None = None,
):
    return build_context(
        pool=pg_pool,
        http_client=make_client(transport),
        api_key="test-key",
        model=model,
        run_id=run_id,
        **({"prompts_dir": prompts_dir} if prompts_dir is not None else {}),
    )


async def test_extract_claims_validates_and_binds_a_scripted_response(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    source = _source("Our Pro plan is $40 per month. A free tier is available.")
    transport = ScriptedTransport(
        {
            CHAT_PATH: [
                _chat_response(
                    [
                        {
                            "attribute": "pricing.entry_usd_month",
                            "value_num": 40,
                            "quote": "$40 per month",
                        },
                        {
                            "attribute": "pricing.made_up",
                            "value_text": "x",
                            "quote": "free tier",
                        },
                    ]
                )
            ]
        }
    )
    ctx = _make_ctx(pg_pool, transport, run_id)

    result = await extract_claims(source, ctx=ctx)

    assert len(result.claims) == 1
    assert result.claims[0].attribute == "pricing.entry_usd_month"
    assert result.claims[0].value_num == 40
    assert result.metrics.claims_returned_by_model == 2
    assert result.metrics.claims_bound == 1
    assert result.metrics.drops.counts[DropReason.INVALID_ATTRIBUTE] == 1


async def test_extract_claims_missing_source_content_raises(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    ctx = _make_ctx(pg_pool, ScriptedTransport(), run_id)
    source = Source(canonical_url="https://example.com/x")

    with pytest.raises(ValueError, match="extracted_text"):
        await extract_claims(source, ctx=ctx)


async def test_cache_hit_makes_zero_llm_calls(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    source = _source("Our Basic plan is $5 per month for solo users.")
    claim = {"attribute": "pricing.entry_usd_month", "value_num": 5, "quote": "$5 per month"}
    transport = ScriptedTransport({CHAT_PATH: [_chat_response([claim])]})
    ctx = _make_ctx(pg_pool, transport, run_id)

    first = await extract_claims(source, ctx=ctx)
    second = await extract_claims(source, ctx=ctx)

    assert transport.calls[CHAT_PATH] == 1
    assert len(first.claims) == 1
    assert len(second.claims) == 1
    assert first.claims[0].quote == second.claims[0].quote


async def test_changed_extractor_version_is_a_cache_miss(pg_pool, tmp_path: Path) -> None:
    """A prompt edit changes `prompt_version` (Phase 05), which changes
    `extractor_version` (this phase), which must miss the extraction cache —
    the same page re-extracted under a different prompt is not the same
    claim provenance."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    source = _source("Our Growth plan is $12 per month per seat.")
    claim = {"attribute": "pricing.entry_usd_month", "value_num": 12, "quote": "$12 per month"}
    transport = ScriptedTransport({CHAT_PATH: [_chat_response([claim]), _chat_response([claim])]})
    prompts_dir_a = tmp_path / "a"
    prompts_dir_a.mkdir()
    (prompts_dir_a / "extract_claims.md").write_text(_PROMPT_TEMPLATE.format(variant="a"))
    prompts_dir_b = tmp_path / "b"
    prompts_dir_b.mkdir()
    (prompts_dir_b / "extract_claims.md").write_text(_PROMPT_TEMPLATE.format(variant="b"))

    ctx_a = _make_ctx(pg_pool, transport, run_id, prompts_dir=prompts_dir_a)
    ctx_b = _make_ctx(pg_pool, transport, run_id, prompts_dir=prompts_dir_b)
    assert extractor_version_for(ctx_a) != extractor_version_for(ctx_b)

    await extract_claims(source, ctx=ctx_a)
    await extract_claims(source, ctx=ctx_b)

    assert transport.calls[CHAT_PATH] == 2


async def test_cached_claims_are_rebound_on_read_not_trusted(pg_pool) -> None:
    """A Phase 03 normalisation drift is caught as a fresh drop, not served
    as a stale span — the phase doc's fail-safe guarantee for the cache."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    ctx = _make_ctx(pg_pool, ScriptedTransport(), run_id)
    extractor_version = extractor_version_for(ctx)
    shared_hash = f"h_{uuid.uuid4().hex}"

    await extraction_cache.put(
        pg_pool,
        content_hash=shared_hash,
        extractor_version=extractor_version,
        claims=[
            RawExtractedClaim(
                attribute="pricing.entry_usd_month", value_num=8, quote="$8 per month"
            )
        ],
    )

    original_source = _source("Our plan is $8 per month.", content_hash=shared_hash)
    result_before = await extract_claims(original_source, ctx=ctx)
    assert len(result_before.claims) == 1

    drifted_source = _source("Our plan is now priced differently.", content_hash=shared_hash)
    result_after = await extract_claims(drifted_source, ctx=ctx)

    assert result_after.claims == []
    assert result_after.metrics.drops.counts[DropReason.QUOTE_NOT_IN_SOURCE] == 1


async def test_untrusted_page_text_reaches_the_model_only_as_a_delimited_block(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _chat_response([])

    ctx = build_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        model=MODEL,
        run_id=run_id,
    )
    source = _source("<script>alert(1)</script> our price is $1/mo")

    await extract_claims(source, ctx=ctx)

    body = captured["body"]
    assert isinstance(body, dict)
    user_content = body["messages"][-1]["content"]
    assert "<untrusted" in user_content
    assert "<script>alert(1)</script>" not in user_content
