"""Entity persistence, alias merging, and the phase's two headline
end-to-end behaviours (Phase 07):

- The `.fly.dev` scenario — the masterplan's own motivating example, and
  this phase's signature test (phase doc, Testing table).
- Concurrent upsert of the same key from two tasks producing one row, no
  error — the phase doc's own "common case, not the edge case" framing.

Every scheme value is uniquified (see `test_verify.py`'s module docstring
for why: `entities`/`entity_aliases`/`verification_cache` are real,
non-rolled-back Postgres tables shared across test runs).
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from _db import insert_run
from _http import PLAIN_HTML, make_client, unique_root
from _http import ScriptedTransport as Transport

from api.models.entity import EntityScheme, Maturity
from api.resolve import resolve_entity, store
from api.resolve.entity_key import derive_gh_key, derive_web_key
from api.resolve.types import EntityEvidence, VerificationContext
from api.retrieval.fetch import HostThrottle
from api.retrieval.robots import RobotsCache
from api.sources.github import GitHubRetriever

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def _slug() -> str:
    return uuid.uuid4().hex[:12]


def _throttle() -> HostThrottle:
    return HostThrottle(concurrency=2, min_gap_s=0.01)


def _web_ctx(pg_pool, transport: Transport) -> VerificationContext:
    client = make_client(transport)
    return VerificationContext(
        pool=pg_pool, http=client, throttle=_throttle(), robots=RobotsCache(client)
    )


def _full_ctx(pg_pool, transport: Transport) -> VerificationContext:
    client = make_client(transport)
    return VerificationContext(
        pool=pg_pool,
        http=client,
        throttle=_throttle(),
        robots=RobotsCache(client),
        github=GitHubRetriever(client, token="test-token"),
    )


def _gh_response(*, homepage: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "full_name": "acme/widget",
            "stargazers_count": 1,
            "open_issues_count": 0,
            "homepage": homepage,
        },
    )


# ---------------------------------------------------------------------------
# resolve_entity: verified -> created; unverified -> discarded (None)
# ---------------------------------------------------------------------------


async def test_resolve_entity_creates_on_verified_artifact(pg_pool) -> None:
    root = unique_root()
    ctx = _web_ctx(pg_pool, Transport({"/": [httpx.Response(200, content=PLAIN_HTML)]}))
    evidence = EntityEvidence(scheme=EntityScheme.WEB, raw_value=root, display_name="Acme")

    entity = await resolve_entity(ctx, evidence)

    assert entity is not None
    assert entity.entity_key == str(derive_web_key(root))


async def test_resolve_entity_discards_unverified_candidate_no_row_created(pg_pool) -> None:
    """ "Discarded and counted" (phase doc): Phase 07's contribution is the
    `None` return — a caller (Phase 10) is responsible for the counting."""
    root = unique_root()
    ctx = _web_ctx(pg_pool, Transport({"/": [httpx.Response(404)]}))
    evidence = EntityEvidence(scheme=EntityScheme.WEB, raw_value=root, display_name="Acme")

    entity = await resolve_entity(ctx, evidence)

    assert entity is None
    count = await pg_pool.fetchval(
        "SELECT count(*) FROM entities WHERE entity_key = $1", str(derive_web_key(root))
    )
    assert count == 0


# ---------------------------------------------------------------------------
# Concurrent upsert: two tasks, same key, one row, no error
# ---------------------------------------------------------------------------


async def test_concurrent_upsert_same_key_yields_one_row(pg_pool) -> None:
    key = f"web:{unique_root()}"

    results = await asyncio.gather(
        store.upsert_entity(pg_pool, entity_key=key, display_name="A", maturity=None, meta={}),
        store.upsert_entity(pg_pool, entity_key=key, display_name="B", maturity=None, meta={}),
    )

    assert len({r.id for r in results}) == 1
    count = await pg_pool.fetchval("SELECT count(*) FROM entities WHERE entity_key = $1", key)
    assert count == 1


# ---------------------------------------------------------------------------
# Alias merge triggers, end to end through resolve_entity
# ---------------------------------------------------------------------------


async def test_gh_homepage_trigger_merges_into_existing_web_entity(pg_pool) -> None:
    root = unique_root()
    repo = _slug()
    web_key = str(derive_web_key(root))
    gh_key = str(derive_gh_key(f"acme/{repo}"))

    web_transport = Transport({"/": [httpx.Response(200, content=PLAIN_HTML)]})
    web_entity = await resolve_entity(
        _web_ctx(pg_pool, web_transport),
        EntityEvidence(scheme=EntityScheme.WEB, raw_value=root, display_name="Acme"),
    )
    assert web_entity is not None

    gh_transport = Transport(
        {
            f"/repos/acme/{repo}": [_gh_response(homepage=f"https://{root}")],
            f"/repos/acme/{repo}/contributors": [httpx.Response(200, json=[])],
        }
    )
    gh_entity = await resolve_entity(
        _full_ctx(pg_pool, gh_transport),
        EntityEvidence(scheme=EntityScheme.GH, raw_value=f"acme/{repo}", display_name="Widget"),
    )

    assert gh_entity is not None
    assert gh_entity.id == web_entity.id
    count = await pg_pool.fetchval(
        "SELECT count(*) FROM entities WHERE entity_key IN ($1, $2)", web_key, gh_key
    )
    assert count == 1


async def test_web_backlink_trigger_merges_existing_gh_entity(pg_pool) -> None:
    root = unique_root()
    repo = _slug()
    web_key = str(derive_web_key(root))
    gh_key = str(derive_gh_key(f"acme/{repo}"))

    gh_transport = Transport(
        {
            f"/repos/acme/{repo}": [_gh_response(homepage=None)],
            f"/repos/acme/{repo}/contributors": [httpx.Response(200, json=[])],
        }
    )
    gh_entity = await resolve_entity(
        _full_ctx(pg_pool, gh_transport),
        EntityEvidence(scheme=EntityScheme.GH, raw_value=f"acme/{repo}", display_name="Widget"),
    )
    assert gh_entity is not None

    web_transport = Transport({"/": [httpx.Response(200, content=PLAIN_HTML)]})
    web_entity = await resolve_entity(
        _web_ctx(pg_pool, web_transport),
        EntityEvidence(
            scheme=EntityScheme.WEB,
            raw_value=root,
            display_name="Acme",
            backlink_repo_url=f"https://github.com/acme/{repo}",
        ),
    )

    assert web_entity is not None
    # Arriving under the now-merged-away gh: alias resolves to the same
    # canonical entity, not a duplicate (phase doc, Testing table).
    async with pg_pool.acquire() as conn:
        resolved_via_alias = await store.find_entity_id(conn, gh_key)
    assert resolved_via_alias == web_entity.id
    count = await pg_pool.fetchval(
        "SELECT count(*) FROM entities WHERE entity_key IN ($1, $2)", web_key, gh_key
    )
    assert count == 1


async def test_package_repository_trigger_merges_npm_into_existing_gh_entity(pg_pool) -> None:
    repo = _slug()
    pkg = _slug()
    gh_key = str(derive_gh_key(f"acme/{repo}"))
    npm_key = f"npm:{pkg}"

    gh_transport = Transport(
        {
            f"/repos/acme/{repo}": [_gh_response(homepage=None)],
            f"/repos/acme/{repo}/contributors": [httpx.Response(200, json=[])],
        }
    )
    gh_entity = await resolve_entity(
        _full_ctx(pg_pool, gh_transport),
        EntityEvidence(scheme=EntityScheme.GH, raw_value=f"acme/{repo}", display_name="Widget"),
    )
    assert gh_entity is not None

    npm_transport = Transport(
        {
            f"/{pkg}": [
                httpx.Response(
                    200,
                    json={
                        "name": pkg,
                        "repository": {"url": f"git+https://github.com/acme/{repo}.git"},
                    },
                )
            ]
        }
    )
    npm_entity = await resolve_entity(
        _web_ctx(pg_pool, npm_transport),
        EntityEvidence(scheme=EntityScheme.NPM, raw_value=pkg, display_name=pkg),
    )

    assert npm_entity is not None
    # gh outranks npm in scheme precedence, so gh stays canonical even
    # though it was discovered first here (order-independence in practice).
    assert npm_entity.id == gh_entity.id
    assert npm_entity.entity_key == gh_key
    count = await pg_pool.fetchval(
        "SELECT count(*) FROM entities WHERE entity_key IN ($1, $2)", gh_key, npm_key
    )
    assert count == 1


async def test_arrival_under_alias_key_updates_canonical_not_a_duplicate(pg_pool) -> None:
    """A later independent discovery path that derives the *same* alias key
    again (e.g. a different task finding the same repo) must update the
    canonical entity, not create a second row."""
    root = unique_root()
    repo = _slug()
    web_key = str(derive_web_key(root))
    gh_key = str(derive_gh_key(f"acme/{repo}"))

    web_entity = await resolve_entity(
        _web_ctx(pg_pool, Transport({"/": [httpx.Response(200, content=PLAIN_HTML)]})),
        EntityEvidence(scheme=EntityScheme.WEB, raw_value=root, display_name="Acme"),
    )
    assert web_entity is not None

    gh_transport = Transport(
        {
            f"/repos/acme/{repo}": [_gh_response(homepage=f"https://{root}")],
            f"/repos/acme/{repo}/contributors": [httpx.Response(200, json=[])],
        }
    )
    await resolve_entity(
        _full_ctx(pg_pool, gh_transport),
        EntityEvidence(scheme=EntityScheme.GH, raw_value=f"acme/{repo}", display_name="Widget"),
    )

    # A later arrival under the (now-merged-away) gh: key directly, as if a
    # second task independently re-derived it.
    reupserted = await store.upsert_entity(
        pg_pool, entity_key=gh_key, display_name="Widget (again)", maturity=None, meta={}
    )

    assert reupserted.id == web_entity.id
    count = await pg_pool.fetchval(
        "SELECT count(*) FROM entities WHERE entity_key IN ($1, $2)", web_key, gh_key
    )
    assert count == 1


# ---------------------------------------------------------------------------
# store.merge_alias: direct branch coverage beyond what resolve_entity's own
# call pattern exercises (it always upserts the candidate's own key before
# ever calling merge_alias, so some of merge_alias's branches are only
# reachable by calling it directly with a hand-built pair).
# ---------------------------------------------------------------------------


async def test_merge_alias_no_op_when_canonical_key_equals_alias_key(pg_pool) -> None:
    key = f"web:{unique_root()}"
    entity = await store.upsert_entity(
        pg_pool, entity_key=key, display_name="Acme", maturity=None, meta={}
    )

    result = await store.merge_alias(pg_pool, canonical_key=key, alias_key=key)

    assert result == entity.id


async def test_merge_alias_returns_none_when_canonical_not_yet_resolved(pg_pool) -> None:
    canonical_key = f"web:{unique_root()}"
    alias_key = f"gh:acme/{_slug()}"

    result = await store.merge_alias(pg_pool, canonical_key=canonical_key, alias_key=alias_key)

    assert result is None
    async with pg_pool.acquire() as conn:
        assert await store.find_entity_id(conn, alias_key) is None


async def test_merge_alias_inserts_alias_directly_when_alias_side_never_independently_resolved(
    pg_pool,
) -> None:
    """The linked side (`backlink_repo_url`'s target) was never
    independently resolved as its own candidate — merge_alias must add a
    plain alias row under the existing canonical, with no duplicate entity
    to collapse."""
    canonical = await store.upsert_entity(
        pg_pool, entity_key=f"web:{unique_root()}", display_name="Acme", maturity=None, meta={}
    )
    alias_key = f"gh:acme/{_slug()}"

    result = await store.merge_alias(
        pg_pool, canonical_key=canonical.entity_key, alias_key=alias_key
    )

    assert result == canonical.id
    async with pg_pool.acquire() as conn:
        assert await store.find_entity_id(conn, alias_key) == canonical.id
    count = await pg_pool.fetchval("SELECT count(*) FROM entities WHERE entity_key = $1", alias_key)
    assert count == 0


async def test_merge_alias_is_idempotent_on_repeated_calls(pg_pool) -> None:
    canonical = await store.upsert_entity(
        pg_pool, entity_key=f"web:{unique_root()}", display_name="Acme", maturity=None, meta={}
    )
    alias = await store.upsert_entity(
        pg_pool, entity_key=f"gh:acme/{_slug()}", display_name="Widget", maturity=None, meta={}
    )

    first = await store.merge_alias(
        pg_pool, canonical_key=canonical.entity_key, alias_key=alias.entity_key
    )
    second = await store.merge_alias(
        pg_pool, canonical_key=canonical.entity_key, alias_key=alias.entity_key
    )

    assert first == canonical.id
    assert second == canonical.id


async def test_merge_alias_repoints_claims_before_deleting_losing_entity(pg_pool) -> None:
    """`claims.entity_id` deliberately has no ON DELETE CASCADE, so a merge
    that deletes the losing entity must first repoint its claims onto the
    canonical — otherwise Postgres raises `claims_entity_id_fkey` (observed
    live on q08 discovery and on cached-only benchmark replay)."""
    canonical = await store.upsert_entity(
        pg_pool, entity_key=f"web:{unique_root()}", display_name="Acme", maturity=None, meta={}
    )
    alias = await store.upsert_entity(
        pg_pool, entity_key=f"gh:acme/{_slug()}", display_name="Widget", maturity=None, meta={}
    )
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        pricing_url = f"https://{_slug()}.com/pricing"
        source_id = await conn.fetchval(
            "INSERT INTO sources (canonical_url, root_key, extracted_text) "
            "VALUES ($1, $2, $3) RETURNING id",
            pricing_url,
            _slug(),
            "Starts at $29/mo per seat.",
        )
        await conn.execute(
            "INSERT INTO claims (run_id, entity_id, source_id, attribute, value_text, "
            "value_num, unit, quote, char_start, char_end, quote_context, context_offset, "
            "grade, confidence, extractor_version) "
            "VALUES ($1, $2, $3, 'pricing.entry_usd_month', NULL, 29, 'usd/month', "
            "'$29/mo per seat', 11, 27, 'Starts at $29/mo per seat.', 0, 'A', 0.9, 'test@1-test')",
            run_id,
            alias.id,
            source_id,
        )

    result = await store.merge_alias(
        pg_pool, canonical_key=canonical.entity_key, alias_key=alias.entity_key
    )

    assert result == canonical.id
    async with pg_pool.acquire() as conn:
        entity_id = await conn.fetchval("SELECT entity_id FROM claims WHERE run_id = $1", run_id)
    assert entity_id == canonical.id


# ---------------------------------------------------------------------------
# The .fly.dev scenario: this phase's signature test
# ---------------------------------------------------------------------------


async def test_fly_dev_scenario_five_distinct_products_all_tiered_hobby(pg_pool) -> None:
    """The masterplan's own motivating example: five distinct products
    hosted on the same PaaS must resolve to five distinct entities, all
    correctly tiered `hobby` — never collapsed into one `fly.dev` entity."""
    hosts = [f"{_slug()}.fly.dev" for _ in range(5)]
    ctx = _web_ctx(pg_pool, Transport({"/": [httpx.Response(200, content=PLAIN_HTML)]}))

    entities = []
    for host in hosts:
        evidence = EntityEvidence(scheme=EntityScheme.WEB, raw_value=host, display_name=host)
        entity = await resolve_entity(ctx, evidence)
        assert entity is not None
        entities.append(entity)

    assert len({e.id for e in entities}) == 5
    assert {e.entity_key for e in entities} == {f"web:{host}" for host in hosts}
    assert all(e.maturity is Maturity.HOBBY for e in entities)
    assert all(e.meta["maturity_rule"] == "hobby:paas_subdomain" for e in entities)
