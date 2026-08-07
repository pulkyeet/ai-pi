"""Contradiction detection & resolution (Phase 08, masterplan §4.7) against
real Postgres. `claims`/`entities`/`sources` are ordinary, non-rolled-back
tables shared across the test session (same pattern as Phase 07's
`test_resolve_store.py`/`test_verify.py`), so every run/entity/source/claim
below is built with a fresh `uuid4` id to keep tests independent.

The final test in this file is the phase's signature test (phase doc,
Testing table): a live pricing page (grade A, fresh) says $5; a 2025
aggregator (grade C, stale) says $18. Contradiction detected, A wins, C is
retained (not deleted) with `superseded_by` set, and the winner's
confidence carries the 0.6 penalty.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import asyncpg
import pytest
from _db import insert_run

from api.evidence.confidence import ConfidenceInputs, confidence
from api.evidence.contradictions import find_contradiction_groups, resolve_contradictions
from api.models.claims import ClaimAttribute, Grade

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


async def _insert_entity(conn: asyncpg.Connection) -> int:
    key = f"web:{uuid.uuid4().hex[:12]}.example.com"
    row = await conn.fetchrow(
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, $2) RETURNING id",
        key,
        "Acme",
    )
    assert row is not None
    return int(row["id"])


async def _insert_source(conn: asyncpg.Connection, *, domain: str | None = None) -> int:
    domain = domain or f"{uuid.uuid4().hex[:12]}.example.com"
    url = f"https://{domain}/pricing/{uuid.uuid4().hex[:8]}"
    row = await conn.fetchrow(
        "INSERT INTO sources (canonical_url, fetched_at, http_status) VALUES ($1, now(), 200) "
        "RETURNING id",
        url,
    )
    assert row is not None
    return int(row["id"])


_char_start = 0


def _next_char_start() -> int:
    global _char_start
    _char_start += 1
    return _char_start


async def _insert_claim(
    conn: asyncpg.Connection,
    *,
    run_id: str,
    entity_id: int,
    source_id: int,
    attribute: str = ClaimAttribute.PRICING_ENTRY_USD_MONTH,
    value_num: float | None = None,
    value_text: str | None = None,
    grade: Grade,
    as_of: date | None = None,
    confidence_inputs: ConfidenceInputs | None = None,
) -> int:
    char_start = _next_char_start()
    inputs_json = confidence_inputs.model_dump_json() if confidence_inputs is not None else None
    row = await conn.fetchrow(
        """
        INSERT INTO claims (run_id, entity_id, attribute, value_text, value_num, source_id,
                             quote, char_start, char_end, quote_context, context_offset,
                             grade, extractor_version, confidence, as_of, confidence_inputs)
        VALUES ($1, $2, $3, $4, $5, $6, 'quote', $7, $7 + 5, 'quote in context', 0,
                $8, 'test@1-test', 0.5, $9, $10::jsonb)
        RETURNING id
        """,
        run_id,
        entity_id,
        attribute,
        value_text,
        value_num,
        source_id,
        char_start,
        grade,
        as_of,
        inputs_json,
    )
    assert row is not None
    return int(row["id"])


async def _claim_row(conn: asyncpg.Connection, claim_id: int) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, superseded_by, confidence, confidence_inputs FROM claims WHERE id = $1",
        claim_id,
    )
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------


async def test_two_prices_for_one_entity_are_detected(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        claim_a = await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_a, value_num=5, grade="A"
        )
        claim_b = await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_b, value_num=18, grade="C"
        )

        groups = await find_contradiction_groups(conn, run_id)

    assert len(groups) == 1
    ids = {row["id"] for row in groups[0]}
    assert ids == {claim_a, claim_b}


async def test_agreeing_claims_are_not_a_contradiction(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_a, value_num=5, grade="A"
        )
        await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_b, value_num=5, grade="B"
        )

        groups = await find_contradiction_groups(conn, run_id)

    assert groups == []


async def test_numeric_tolerance_five_dollars_vs_five_zero_zero_is_not_a_contradiction(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_a, value_num=5, grade="A"
        )
        await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_b, value_num=5.00, grade="B"
        )

        groups = await find_contradiction_groups(conn, run_id)

    assert groups == []


async def test_grade_d_is_excluded_from_detection(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_a, value_num=5, grade="D"
        )
        await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_b, value_num=18, grade="D"
        )

        groups = await find_contradiction_groups(conn, run_id)

    assert groups == []


async def test_text_attributes_contradict_on_normalised_value_text(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_a,
            attribute=ClaimAttribute.PRICING_MODEL,
            value_text="seat",
            grade="A",
        )
        await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_b,
            attribute=ClaimAttribute.PRICING_MODEL,
            value_text="usage",
            grade="B",
        )

        groups = await find_contradiction_groups(conn, run_id)

    assert len(groups) == 1
    assert groups[0][0]["attribute"] == ClaimAttribute.PRICING_MODEL


async def test_text_attribute_formatting_alone_is_not_a_contradiction(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_a,
            attribute=ClaimAttribute.COMPANY_STAGE,
            value_text="Seed",
            grade="A",
        )
        await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_b,
            attribute=ClaimAttribute.COMPANY_STAGE,
            value_text="  seed ",
            grade="B",
        )

        groups = await find_contradiction_groups(conn, run_id)

    assert groups == []


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


async def test_resolution_picks_highest_grade_regardless_of_recency(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        claim_a = await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_a,
            value_num=5,
            grade="A",
            as_of=date(2020, 1, 1),
            confidence_inputs=ConfidenceInputs(
                best_grade=Grade.A, n_distinct_domains=1, age_days=2000, contradicted=False
            ),
        )
        claim_b = await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_b,
            value_num=18,
            grade="B",
            as_of=date(2026, 1, 1),
        )

    resolutions = await resolve_contradictions(pg_pool, run_id)

    assert len(resolutions) == 1
    assert resolutions[0].winner_id == claim_a
    assert resolutions[0].loser_ids == (claim_b,)


async def test_resolution_tiebreaks_on_most_recent_as_of(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        claim_old = await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_a,
            value_num=5,
            grade="A",
            as_of=date(2024, 1, 1),
        )
        claim_recent = await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=source_b,
            value_num=8,
            grade="A",
            as_of=date(2026, 1, 1),
        )

    resolutions = await resolve_contradictions(pg_pool, run_id)

    assert resolutions[0].winner_id == claim_recent
    assert resolutions[0].loser_ids == (claim_old,)


async def test_loser_is_retained_not_deleted_and_superseded_by_is_set(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        winner = await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_a, value_num=5, grade="A"
        )
        loser = await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_b, value_num=18, grade="C"
        )

    await resolve_contradictions(pg_pool, run_id)

    async with pg_pool.acquire() as conn:
        loser_row = await _claim_row(conn, loser)
        winner_row = await _claim_row(conn, winner)

    assert loser_row["superseded_by"] == winner
    assert winner_row["superseded_by"] is None


async def test_missing_confidence_inputs_does_not_crash_resolution(pg_pool: asyncpg.Pool) -> None:
    """A claim written before this phase's `confidence_inputs` column was
    populated (or by a caller that didn't set it) must not break
    resolution — the penalty is simply skipped for that winner, logged, and
    every other part of resolution (loser retention) still happens."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_a = await _insert_source(conn)
        source_b = await _insert_source(conn)
        winner = await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_a, value_num=5, grade="A"
        )
        loser = await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_b, value_num=18, grade="C"
        )

    resolutions = await resolve_contradictions(pg_pool, run_id)

    assert resolutions[0].winner_id == winner
    assert resolutions[0].loser_ids == (loser,)


# ---------------------------------------------------------------------------
# the trap case — this phase's signature test
# ---------------------------------------------------------------------------


async def test_the_trap_case_live_pricing_page_vs_stale_aggregator(
    pg_pool: asyncpg.Pool,
) -> None:
    """Masterplan's own worked example: a live pricing page (grade A,
    fresh) says $5; a 2025 aggregator review (grade C, stale) says $18.
    Detected, A wins, C is retained with `superseded_by` set, and A's
    confidence carries the 0.6 contradiction penalty."""
    live_inputs = ConfidenceInputs(
        best_grade=Grade.A, n_distinct_domains=1, age_days=7, contradicted=False
    )
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        live_source = await _insert_source(conn)
        aggregator_source = await _insert_source(conn)

        live_claim = await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=live_source,
            value_num=5,
            grade="A",
            as_of=date(2026, 7, 30),
            confidence_inputs=live_inputs,
        )
        stale_claim = await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_id=aggregator_source,
            value_num=18,
            grade="C",
            as_of=date(2025, 11, 2),
        )

        groups = await find_contradiction_groups(conn, run_id)

    assert len(groups) == 1
    assert {r["id"] for r in groups[0]} == {live_claim, stale_claim}

    resolutions = await resolve_contradictions(pg_pool, run_id)

    assert len(resolutions) == 1
    assert resolutions[0].winner_id == live_claim
    assert resolutions[0].loser_ids == (stale_claim,)

    async with pg_pool.acquire() as conn:
        stale_row = await _claim_row(conn, stale_claim)
        live_row = await _claim_row(conn, live_claim)

    # loser retained, not deleted, and points at the winner
    assert stale_row["superseded_by"] == live_claim
    assert live_row["superseded_by"] is None

    # winner's confidence carries the 0.6 penalty, recomputed from the
    # inputs stored at claim-construction time
    expected = confidence(live_inputs.model_copy(update={"contradicted": True}))
    assert float(live_row["confidence"]) == pytest.approx(expected)
    persisted_inputs = json.loads(live_row["confidence_inputs"])
    assert persisted_inputs["contradicted"] is True
