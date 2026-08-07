"""Entity persistence — idempotent upsert on `entities`/`entity_aliases`
(schema from migration `0001_initial`, Phase 00). Safe to call concurrently
from multiple task handlers: masterplan's real-world case is two tasks
discovering the same competitor at the same time, not the exception (phase
doc, Design section).

`entity_key` derivation (`api.resolve.entity_key`) and verification
(`api.resolve.verify`) both operate on `EntityKey`/`str`; this module is
the one place that talks to Postgres, via plain `asyncpg` + hand-written
SQL, matching every other `api.*` persistence module in this codebase.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import structlog
from pydantic import BaseModel

from api.models.entity import Maturity

logger = structlog.get_logger()


class Entity(BaseModel):
    id: int
    entity_key: str
    display_name: str
    maturity: Maturity | None
    meta: dict[str, Any]


def _row_to_entity(row: asyncpg.Record) -> Entity:
    meta = row["meta"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return Entity(
        id=row["id"],
        entity_key=row["entity_key"],
        display_name=row["display_name"],
        maturity=row["maturity"],
        meta=meta,
    )


async def find_entity_id(conn: asyncpg.Connection, key: str) -> int | None:
    """Resolve a raw key string to its current entity id — checking the
    canonical `entity_key` first, then `entity_aliases`, so an arrival
    under a merged-away alias resolves to the canonical entity instead of
    creating a duplicate."""
    row = await conn.fetchrow("SELECT id FROM entities WHERE entity_key = $1", key)
    if row is not None:
        return int(row["id"])
    row = await conn.fetchrow("SELECT entity_id FROM entity_aliases WHERE alias_key = $1", key)
    return int(row["entity_id"]) if row is not None else None


async def get_entity(pool: asyncpg.Pool, entity_id: int) -> Entity | None:
    row = await pool.fetchrow(
        "SELECT id, entity_key, display_name, maturity, meta FROM entities WHERE id = $1",
        entity_id,
    )
    return _row_to_entity(row) if row is not None else None


async def upsert_entity(
    pool: asyncpg.Pool,
    *,
    entity_key: str,
    display_name: str,
    maturity: Maturity | None,
    meta: dict[str, Any],
) -> Entity:
    """`ON CONFLICT (entity_key) DO UPDATE` makes the common case — two
    concurrent tasks resolving the same canonical key — a single atomic
    statement with no read-then-write race. The `find_entity_id` branch
    below only matters for the *other* case: `entity_key` isn't itself in
    `entities` yet, but it's already recorded as someone else's alias (an
    earlier merge chose a different canonical key for this product) — a
    plain `ON CONFLICT (entity_key)` would miss that and create a
    duplicate, so that path is checked first, still inside one
    transaction."""
    meta_json = json.dumps(meta)
    async with pool.acquire() as conn, conn.transaction():
        existing_id = await find_entity_id(conn, entity_key)
        if existing_id is not None:
            row = await conn.fetchrow(
                """
                UPDATE entities
                SET display_name = $2, maturity = $3, meta = meta || $4::jsonb
                WHERE id = $1
                RETURNING id, entity_key, display_name, maturity, meta
                """,
                existing_id,
                display_name,
                maturity,
                meta_json,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO entities (entity_key, display_name, maturity, meta)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (entity_key) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    maturity     = EXCLUDED.maturity,
                    meta         = entities.meta || EXCLUDED.meta
                RETURNING id, entity_key, display_name, maturity, meta
                """,
                entity_key,
                display_name,
                maturity,
                meta_json,
            )
    assert row is not None
    return _row_to_entity(row)


async def merge_alias(pool: asyncpg.Pool, *, canonical_key: str, alias_key: str) -> int | None:
    """Ensure `alias_key` resolves to the same entity as `canonical_key`.

    Idempotent and safe to call repeatedly with the same pair, from either
    arrival order, because `api.resolve.alias.canonical_pair` always
    computes the same `canonical_key` for a given pair of schemes
    regardless of which candidate was resolved first. Returns the merged
    entity id, or `None` if `canonical_key` has no entity yet — that side
    of the trigger just hasn't been independently resolved yet, which is
    not an error: the same edge merges successfully once it has been (see
    `api.resolve.alias`'s module docstring).
    """
    if canonical_key == alias_key:
        async with pool.acquire() as conn:
            return await find_entity_id(conn, canonical_key)

    # Lock in a fixed, key-derived order so two concurrent merges touching
    # the same pair of keys can't deadlock on each other.
    first_key, second_key = sorted((canonical_key, alias_key))

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT id FROM entities WHERE entity_key = $1 FOR UPDATE", first_key)
        await conn.execute("SELECT id FROM entities WHERE entity_key = $1 FOR UPDATE", second_key)

        canonical_id = await find_entity_id(conn, canonical_key)
        if canonical_id is None:
            logger.debug(
                "resolve.merge_alias.canonical_not_yet_resolved",
                canonical_key=canonical_key,
                alias_key=alias_key,
            )
            return None

        alias_id = await find_entity_id(conn, alias_key)
        if alias_id is None:
            await conn.execute(
                "INSERT INTO entity_aliases (entity_id, alias_key) VALUES ($1, $2) "
                "ON CONFLICT (alias_key) DO NOTHING",
                canonical_id,
                alias_key,
            )
            return canonical_id

        if alias_id == canonical_id:
            return canonical_id

        # A real duplicate: `alias_key` already resolved to its own entity
        # (e.g. discovered before the linking evidence arrived). Collapse
        # it into the canonical entity — merges only ever collapse, never
        # split (phase doc, Design section).
        alias_own_key = await conn.fetchval(
            "SELECT entity_key FROM entities WHERE id = $1", alias_id
        )
        await conn.execute(
            "UPDATE entity_aliases SET entity_id = $1 WHERE entity_id = $2", canonical_id, alias_id
        )
        await conn.execute(
            "INSERT INTO entity_aliases (entity_id, alias_key) VALUES ($1, $2) "
            "ON CONFLICT (alias_key) DO NOTHING",
            canonical_id,
            alias_own_key,
        )
        await conn.execute("DELETE FROM entities WHERE id = $1", alias_id)
        return canonical_id


__all__ = ["Entity", "find_entity_id", "get_entity", "merge_alias", "upsert_entity"]
