"""Contradiction detection & resolution (masterplan §4.7, decision log #6):

> select entity_id, attribute, array_agg(distinct value_num), count(*)
>   from claims
>  where run_id = $1 and superseded_by is null and grade in ('A','B','C')
>  group by entity_id, attribute
> having count(distinct value_num) > 1;

Grade D is excluded — "two Reddit comments disagreeing about a price is
noise, not a contradiction". Resolution: highest grade wins, ties broken by
most recent `as_of`. The loser is **retained and surfaced**, not deleted —
"pricing page says $5 last week, a 2025 review says $18" is genuinely
useful signal — via `superseded_by`, and the winner's confidence takes the
0.6 contradiction penalty (Open Decision #1: currently the winner, since
the loser isn't scored for display).

One deliberate extension beyond the literal query above: `value_num` is
Postgres `numeric` (exact decimal, not float), so a plain `count(distinct
value_num)` already treats `$5.00` and `$5` as equal — no extra tolerance
logic is needed for numeric attributes. But the literal query only ever
looks at `value_num`, and the closed vocabulary has plenty of non-numeric
attributes (`pricing.model`, `company.stage`, `product.launch_date`, ...).
Those compare on **normalised** `value_text` instead — trimmed and
lowercased, so formatting alone (`"Seed"` vs `"seed"`) never manufactures a
false contradiction. This is `ATTRIBUTE_SPEC`-driven, per the phase doc's
own "the comparison rule is per-attribute" instruction, computed in Python
rather than SQL `DISTINCT` because the per-attribute rule (numeric vs.
normalised text) isn't expressible in one `GROUP BY`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from itertools import groupby
from typing import Any

import asyncpg
import structlog

from api.evidence.confidence import ConfidenceInputs, confidence
from api.models.claims import ValueKind, attribute_spec

logger = structlog.get_logger()

# Masterplan §4.7: highest grade wins. Lower rank is better, matching
# `api.evidence.grade`'s own convention.
_GRADE_RANK: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}


@dataclass(frozen=True)
class ContradictionResolution:
    entity_id: int
    attribute: str
    winner_id: int
    loser_ids: tuple[int, ...]


def _comparison_key(attribute: str, row: asyncpg.Record) -> tuple[str, Any]:
    spec = attribute_spec(attribute)
    if spec.kind is ValueKind.NUMERIC:
        return ("num", row["value_num"])
    return ("text", (row["value_text"] or "").strip().lower())


def _is_contradictory(attribute: str, rows: list[asyncpg.Record]) -> bool:
    return len({_comparison_key(attribute, r) for r in rows}) > 1


async def find_contradiction_groups(
    conn: asyncpg.Connection, run_id: str
) -> list[list[asyncpg.Record]]:
    """The masterplan §4.7 `GROUP BY`, with grade D excluded and the
    per-attribute distinctness rule applied. Returns one list of claim rows
    per genuinely contradictory `(entity_id, attribute)` group; groups with
    only one surviving claim, or where every value in the group agrees
    under the attribute's comparison rule, are not returned at all.
    """
    rows = await conn.fetch(
        """
        SELECT id, entity_id, attribute, value_text, value_num, grade, as_of, confidence_inputs
          FROM claims
         WHERE run_id = $1 AND superseded_by IS NULL AND grade IN ('A', 'B', 'C')
         ORDER BY entity_id, attribute
        """,
        run_id,
    )
    groups = [
        list(group) for _, group in groupby(rows, key=lambda r: (r["entity_id"], r["attribute"]))
    ]
    return [g for g in groups if len(g) > 1 and _is_contradictory(g[0]["attribute"], g)]


def _pick_winner(rows: list[asyncpg.Record]) -> asyncpg.Record:
    """Highest grade wins; ties broken by most recent `as_of` (masterplan
    §4.7). An undated claim sorts as older than any dated one — it only
    wins a tie against another undated claim. `id` is a final, fully
    deterministic tiebreak for the (unlikely) case of an exact tie on both
    grade and `as_of`.
    """

    def sort_key(r: asyncpg.Record) -> tuple[int, int, int]:
        as_of: date | None = r["as_of"]
        return (_GRADE_RANK[r["grade"]], -(as_of or date.min).toordinal(), r["id"])

    return min(rows, key=sort_key)


def _decode_confidence_inputs(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        result: dict[str, Any] = json.loads(raw)
        return result
    if isinstance(raw, dict):
        return raw
    raise TypeError(f"unexpected confidence_inputs type: {type(raw)!r}")


async def _apply_contradiction_penalty(conn: asyncpg.Connection, winner: asyncpg.Record) -> None:
    """Recomputes the winner's confidence with `contradicted=True`, reusing
    the inputs stored at claim-construction time — the existence of
    disagreement is itself evidence of uncertainty, so a resolved
    contradiction still lowers the surfaced winner's confidence even though
    it "won" (masterplan §4.6/§4.7)."""
    raw_inputs = _decode_confidence_inputs(winner["confidence_inputs"])
    if raw_inputs is None:
        logger.warning(
            "evidence.contradiction.missing_confidence_inputs",
            claim_id=winner["id"],
            entity_id=winner["entity_id"],
            attribute=winner["attribute"],
        )
        return
    inputs = ConfidenceInputs.model_validate(raw_inputs).model_copy(update={"contradicted": True})
    new_confidence = confidence(inputs)
    await conn.execute(
        "UPDATE claims SET confidence = $1, confidence_inputs = $2::jsonb WHERE id = $3",
        new_confidence,
        inputs.model_dump_json(),
        winner["id"],
    )


async def resolve_contradictions(pool: asyncpg.Pool, run_id: str) -> list[ContradictionResolution]:
    """Detect and resolve every contradiction for one run: losers are
    retained with `superseded_by` set (never deleted), and the winner's
    confidence is recomputed under the 0.6 contradiction penalty. Runs
    inside one transaction so a run's contradiction pass is all-or-nothing.
    """
    resolutions = []
    async with pool.acquire() as conn, conn.transaction():
        groups = await find_contradiction_groups(conn, run_id)
        for rows in groups:
            winner = _pick_winner(rows)
            losers = [r for r in rows if r["id"] != winner["id"]]
            await conn.executemany(
                "UPDATE claims SET superseded_by = $1 WHERE id = $2",
                [(winner["id"], loser["id"]) for loser in losers],
            )
            await _apply_contradiction_penalty(conn, winner)
            resolutions.append(
                ContradictionResolution(
                    entity_id=winner["entity_id"],
                    attribute=winner["attribute"],
                    winner_id=winner["id"],
                    loser_ids=tuple(loser["id"] for loser in losers),
                )
            )
    return resolutions


__all__ = [
    "ContradictionResolution",
    "find_contradiction_groups",
    "resolve_contradictions",
]
