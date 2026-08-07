"""Phase 11's signature test — full report assembly from seeded claims,
against real Postgres and the real prompt files (`src/api/prompts/
synthesise_*.md`), with scripted (not live) embeddings/chat transports.

Claims are seeded directly (mirroring `test_contradictions.py`'s own
pattern), not produced via `api.tasks`/the executor — this phase consumes
claims already in the database; it does not produce them, and Phase 10
already proves the producing side end to end.

Every seeded claim's `quote`/`char_start`/`char_end` is computed from the
real source text via `str.find`, exactly like `bind_span` — so the "100%
sentence binding" test at the bottom can walk every claim the assembled
report cites, back to its real source text, and assert the quote is
actually there at the recorded span.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date

import asyncpg
import httpx
import pytest
from _db import insert_run

from api.evidence.coverage import CoverageResult
from api.llm.embed import EMBEDDING_DIM, build_embed_context
from api.llm.gateway import build_context
from api.models.brief import ResearchBrief
from api.models.report import Report
from api.synth.assemble import RunMeta, assemble_report

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

CHAT_PATH = "/api/v1/chat/completions"
EMBEDDINGS_PATH = "/api/v1/embeddings"


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------


async def _insert_entity(conn: asyncpg.Connection, *, display_name: str, key: str) -> int:
    row = await conn.fetchrow(
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, $2) RETURNING id",
        key,
        display_name,
    )
    assert row is not None
    return int(row["id"])


async def _insert_source(conn: asyncpg.Connection, text: str) -> int:
    url = f"https://{uuid.uuid4().hex[:12]}.example.com/page"
    row = await conn.fetchrow(
        "INSERT INTO sources (canonical_url, fetched_at, http_status, extracted_text, "
        "content_hash) VALUES ($1, now(), 200, $2, $3) RETURNING id",
        url,
        text,
        hashlib.sha256(text.encode()).hexdigest(),
    )
    assert row is not None
    return int(row["id"])


async def _insert_claim(
    conn: asyncpg.Connection,
    *,
    run_id: str,
    entity_id: int,
    source_id: int,
    source_text: str,
    attribute: str,
    quote: str,
    value_text: str | None = None,
    value_num: float | None = None,
    grade: str = "A",
    as_of: date | None = None,
    confidence: float = 0.8,
    superseded_by: int | None = None,
) -> int:
    idx = source_text.find(quote)
    assert idx != -1, f"quote {quote!r} not literally present in the seeded source text"
    char_start, char_end = idx, idx + len(quote)
    row = await conn.fetchrow(
        """
        INSERT INTO claims (run_id, entity_id, attribute, value_text, value_num, source_id,
                             quote, char_start, char_end, quote_context, context_offset,
                             grade, extractor_version, confidence, as_of, superseded_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,0,$11,'test@1-test',$12,$13,$14)
        RETURNING id
        """,
        run_id,
        entity_id,
        attribute,
        value_text,
        value_num,
        source_id,
        quote,
        char_start,
        char_end,
        source_text,
        grade,
        confidence,
        as_of,
        superseded_by,
    )
    assert row is not None
    return int(row["id"])


async def _seed_competitor(
    conn: asyncpg.Connection,
    run_id: str,
    *,
    name: str,
    model: str,
    entry_usd_month: float,
    free_tier: bool,
) -> int:
    key = f"web:{uuid.uuid4().hex[:10]}.com"
    entity_id = await _insert_entity(conn, display_name=name, key=key)
    free_tier_text = "a free tier is offered" if free_tier else "no free tier is offered"
    text = (
        f"{name} pricing page. Billing is {model} based pricing here. "
        f"Plans start at ${entry_usd_month:.0f} per month. {free_tier_text}. "
        "Available on web and ios platforms."
    )
    source_id = await _insert_source(conn, text)
    await _insert_claim(
        conn,
        run_id=run_id,
        entity_id=entity_id,
        source_id=source_id,
        source_text=text,
        attribute="pricing.model",
        quote=f"{model} based pricing here",
        value_text=model,
    )
    await _insert_claim(
        conn,
        run_id=run_id,
        entity_id=entity_id,
        source_id=source_id,
        source_text=text,
        attribute="pricing.entry_usd_month",
        quote=f"${entry_usd_month:.0f} per month",
        value_num=entry_usd_month,
        as_of=date(2026, 7, 1),
    )
    await _insert_claim(
        conn,
        run_id=run_id,
        entity_id=entity_id,
        source_id=source_id,
        source_text=text,
        attribute="pricing.free_tier",
        quote=free_tier_text,
        value_text="true" if free_tier else "false",
    )
    await _insert_claim(
        conn,
        run_id=run_id,
        entity_id=entity_id,
        source_id=source_id,
        source_text=text,
        attribute="product.platforms",
        quote="web",
    )
    return entity_id


async def _seed_theme(
    conn: asyncpg.Connection,
    run_id: str,
    category_entity_id: int,
    *,
    attribute_family: str,
    slug: str,
    count: int = 5,
    distinct_sources: int = 3,
) -> list[int]:
    """`count` claims of `{attribute_family}.{slug}` spread across exactly
    `distinct_sources` distinct synthetic sources (round-robin, each source
    created once and reused) — enough to satisfy
    `api.evidence.promotion.evaluate_community_theme`'s 5-support/
    3-distinct-thread bar at the default settings. Each claim's quote is
    uniquely numbered so two claims sharing one source never collide on
    `claims_unique_span (run_id, source_id, attribute, char_start)`."""
    per_source: dict[int, list[int]] = {i: [] for i in range(distinct_sources)}
    for i in range(count):
        per_source[i % distinct_sources].append(i)

    claim_ids = []
    for source_index, claim_indices in per_source.items():
        mentions = "; ".join(f"mention {j} of {slug} being a real issue" for j in claim_indices)
        text = f"Community thread {source_index}: {mentions}."
        source_id = await _insert_source(conn, text)
        for j in claim_indices:
            quote = f"mention {j} of {slug} being a real issue"
            claim_id = await _insert_claim(
                conn,
                run_id=run_id,
                entity_id=category_entity_id,
                source_id=source_id,
                source_text=text,
                attribute=f"{attribute_family}.{slug}",
                quote=quote,
                value_text=slug,
                grade="D",
                confidence=0.4,
            )
            claim_ids.append(claim_id)
    return claim_ids


# ---------------------------------------------------------------------------
# scripted transports
# ---------------------------------------------------------------------------


def _embed_handler_factory() -> object:
    slug_index: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        vectors = []
        for text in body["input"]:
            slug = text.split(":", 1)[0]
            idx = slug_index.setdefault(slug, len(slug_index))
            vec = [0.0] * EMBEDDING_DIM
            vec[idx % EMBEDDING_DIM] = 1.0
            vectors.append(vec)
        return httpx.Response(
            200, json={"data": [{"embedding": v} for v in vectors], "usage": {"prompt_tokens": 5}}
        )

    return handler


_FINDING_LINE_RE = re.compile(r"^\[(\d+)\] kind=(\S+)", re.MULTILINE)


def _findings_from_body(body: dict) -> list[tuple[int, str]]:
    content = body["messages"][-1]["content"]
    return [(int(m.group(1)), m.group(2)) for m in _FINDING_LINE_RE.finditer(content)]


def _chat_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


async def _valid_chat_handler(request: httpx.Request) -> httpx.Response:
    """Always returns a domain-valid response on the first attempt — cites
    >=3 distinct findings including one pain_point, one bracketed marker per
    sentence, matching what `api.synth.generate`'s prompts require."""
    body = json.loads(request.content)
    system_content = body["messages"][0]["content"]
    findings = _findings_from_body(body)
    pain_ids = [fid for fid, kind in findings if kind == "pain_point"]
    all_ids = [fid for fid, _ in findings]
    assert pain_ids, "test setup must seed at least one pain_point finding"
    cited = list(dict.fromkeys([pain_ids[0], *all_ids]))[:3]
    ids_str = ", ".join(str(i) for i in cited)
    statement = f"Grounded synthesis statement citing real findings [{ids_str}]."

    if "MVP-synthesis" in system_content:
        payload = {"statement": statement, "addresses_finding_ids": cited}
    elif "feature-gap-synthesis" in system_content:
        payload = {"gaps": [{"statement": statement, "addresses_finding_ids": cited}]}
    elif "risk-synthesis" in system_content:
        payload = {"risks": [{"statement": statement, "addresses_finding_ids": cited}]}
    else:
        raise AssertionError(f"unrecognised synthesis prompt: {system_content[:120]!r}")

    return _chat_response(payload)


# ---------------------------------------------------------------------------
# the full pipeline
# ---------------------------------------------------------------------------


async def test_full_assembly_from_seeded_claims_produces_a_valid_report(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        category_entity_id = await _insert_entity(
            conn, display_name="Community signal", key=f"category:{run_id}"
        )

        acme_id = await _seed_competitor(
            conn, run_id, name="Acme", model="seat", entry_usd_month=5, free_tier=True
        )
        await _seed_competitor(
            conn, run_id, name="Beta", model="flat", entry_usd_month=15, free_tier=False
        )

        # a contradiction: a second, stale, lower-grade claim on Acme's
        # entry price, already resolved (superseded_by set) exactly as
        # api.evidence.contradictions would leave it
        stale_source_text = "An old 2025 review says Acme costs $8 per month now discontinued."
        stale_source_id = await _insert_source(conn, stale_source_text)
        winner_row = await conn.fetchrow(
            "SELECT id FROM claims WHERE entity_id = $1 AND attribute = 'pricing.entry_usd_month'",
            acme_id,
        )
        assert winner_row is not None
        await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=acme_id,
            source_id=stale_source_id,
            source_text=stale_source_text,
            attribute="pricing.entry_usd_month",
            quote="$8 per month",
            value_num=8,
            grade="C",
            as_of=date(2025, 11, 1),
            superseded_by=winner_row["id"],
        )

        await _seed_theme(
            conn, run_id, category_entity_id, attribute_family="complaint", slug="manual-entry"
        )
        await _seed_theme(
            conn, run_id, category_entity_id, attribute_family="request", slug="bulk-export"
        )

    llm_ctx = build_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_valid_chat_handler)),
        api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        run_id=run_id,
    )
    embed_ctx = build_embed_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_embed_handler_factory())),
        api_key="test-key",
        run_id=run_id,
    )
    brief = ResearchBrief(category="c", segment="s", geography="g", monetisation_guess="m")
    coverage = CoverageResult(
        score=0.75,
        failed_branches=("funding",),
        budget_skipped_branches=(),
        other_skipped_branches=(),
    )
    meta = RunMeta(cost_usd=0.01, duration_s=12.3, sources_fetched=5, cache_hit_rate=0.4)

    report = await assemble_report(
        pg_pool,
        run_id=run_id,
        query="AI expense tracker",
        brief=brief,
        llm_ctx=llm_ctx,
        embed_ctx=embed_ctx,
        coverage=coverage,
        meta=meta,
    )

    # --- contract shape, real numbers ---
    assert report.run_id == run_id
    assert len(report.competitors) == 2
    assert {c.display_name for c in report.competitors} == {"Acme", "Beta"}
    assert report.pricing_landscape.claim_ids

    assert len(report.pain_points) == 1
    assert report.pain_points[0].theme == "manual-entry"
    assert report.pain_points[0].support_count == 5
    assert report.pain_points[0].distinct_threads == 3
    assert report.pain_points[0].grade == "D"

    assert len(report.feature_gaps) == 1
    assert report.feature_gaps[0].addresses_finding_ids

    assert report.mvp.statement != ""
    assert report.mvp.addresses_finding_ids

    assert len(report.risks) == 1

    assert "funding" in report.coverage.failed_branches

    assert len(report.contradictions) == 1
    contradiction = report.contradictions[0]
    assert contradiction.attribute == "pricing.entry_usd_month"
    assert {v.v for v in contradiction.values} == {5.0, 8.0}

    # --- round-trips to JSON unchanged (contract stability) ---
    round_tripped = Report.model_validate_json(report.model_dump_json())
    assert round_tripped == report

    # --- 100% sentence/citation binding: every claim id the report cites,
    # directly or via a finding, resolves to a real span in real source text
    async with pg_pool.acquire() as conn:
        finding_rows = await conn.fetch(
            "SELECT id, claim_ids FROM findings WHERE run_id = $1", run_id
        )
    finding_claim_ids = {r["id"]: list(r["claim_ids"]) for r in finding_rows}

    cited_claim_ids: set[int] = set()
    cited_claim_ids.update(report.pricing_landscape.claim_ids)
    for pp in report.pain_points:
        cited_claim_ids.update(pp.claim_ids)
    for comp in report.competitors:
        cited_claim_ids.update(comp.claim_ids)
    for fid in report.mvp.addresses_finding_ids:
        cited_claim_ids.update(finding_claim_ids[fid])
    for gap in report.feature_gaps:
        for fid in gap.addresses_finding_ids:
            cited_claim_ids.update(finding_claim_ids[fid])
    for risk in report.risks:
        for fid in risk.addresses_finding_ids:
            cited_claim_ids.update(finding_claim_ids[fid])
    for contradiction in report.contradictions:
        cited_claim_ids.update(v.src for v in contradiction.values)

    assert cited_claim_ids, "the report must cite at least one real claim"

    async with pg_pool.acquire() as conn:
        for claim_id in cited_claim_ids:
            row = await conn.fetchrow(
                "SELECT c.quote, c.char_start, c.char_end, s.extracted_text "
                "FROM claims c JOIN sources s ON s.id = c.source_id WHERE c.id = $1",
                claim_id,
            )
            assert row is not None, f"claim {claim_id} cited by the report does not exist"
            span_text = row["extracted_text"][row["char_start"] : row["char_end"]]
            assert span_text == row["quote"], (
                f"claim {claim_id}: span text {span_text!r} != stored quote {row['quote']!r}"
            )

    # --- persisted, retrievable by run_id ---
    async with pg_pool.acquire() as conn:
        payload = await conn.fetchval("SELECT payload FROM reports WHERE run_id = $1", run_id)
    assert payload is not None
    assert Report.model_validate_json(payload) == report


async def test_generic_advice_regression_no_complaints_means_no_mvp_section(
    pg_pool: asyncpg.Pool,
) -> None:
    """The guard firing, not just described: a run with real competitors and
    real pricing but zero complaint-derived findings must produce no MVP
    section, no feature gaps, no risks — and must not spend an LLM call
    finding that out, since the outcome is already determined."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        await _seed_competitor(
            conn, run_id, name="Acme", model="seat", entry_usd_month=5, free_tier=True
        )
        await _seed_competitor(
            conn, run_id, name="Beta", model="flat", entry_usd_month=15, free_tier=False
        )
        # no complaint.*/request.* claims seeded at all

    def _fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected call to {request.url.path} — guard should short-circuit")

    llm_ctx = build_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_fail)),
        api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        run_id=run_id,
    )
    embed_ctx = build_embed_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_fail)),
        api_key="test-key",
        run_id=run_id,
    )
    brief = ResearchBrief(category="c", segment="s", geography="g", monetisation_guess="m")
    coverage = CoverageResult(
        score=1.0, failed_branches=(), budget_skipped_branches=(), other_skipped_branches=()
    )
    meta = RunMeta(cost_usd=0.0, duration_s=1.0, sources_fetched=2, cache_hit_rate=1.0)

    report = await assemble_report(
        pg_pool,
        run_id=run_id,
        query="q",
        brief=brief,
        llm_ctx=llm_ctx,
        embed_ctx=embed_ctx,
        coverage=coverage,
        meta=meta,
    )

    assert report.mvp.statement == ""
    assert report.mvp.addresses_finding_ids == []
    assert report.feature_gaps == []
    assert report.risks == []
    assert set(report.coverage.failed_branches) == {
        "mvp_synthesis",
        "feature_gaps_synthesis",
        "risks_synthesis",
    }
    # competitors/pricing are unaffected — the guard is specific to synthesis
    assert len(report.competitors) == 2
