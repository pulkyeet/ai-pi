"""Phase 14: `python -m bench.runner` — execute, score, and record.

```
python -m bench.runner --tuning                 # the six tuning queries (default)
python -m bench.runner --held-out --confirm      # the four held-out queries, once
python -m bench.runner --query q05               # a single query by id
python -m bench.runner --tuning --cached-only    # refuse any live network call
python -m bench.runner --export-cache-seed --tuning
```

Each query runs through the real pipeline in-process, via `api.cli.run_query`
(Phase 14's own extraction — the same code path `python -m api.cli run`
takes, not a second copy of it), scored against its `ground_truth` by
`bench.metrics`, and written as a dated JSON snapshot under `bench/results/`.

`--cached-only` needs no change anywhere in `src/api/`: every layer's own
cache (source, search, extraction, LLM response — masterplan §9) is checked
*before* a real HTTP call is ever attempted, so a genuinely warm run never
reaches the transport at all. Swapping in a transport that raises on any
request it does see is therefore a complete, honest "did this actually cost
nothing" proof — not a mocked shortcut.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import asyncpg
import httpx
import structlog

from api.cli import RunOutcome, run_query
from api.config import Settings
from api.db import create_pool
from api.retrieval.fetch import build_client
from bench import metrics
from bench.loader import (
    BenchmarkQuery,
    load_all_queries,
    load_held_out_queries,
    load_tuning_queries,
)

logger = structlog.get_logger()

RESULTS_DIR = Path(__file__).parent / "results"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
CACHE_SEED_TABLES = (
    "sources",
    "path_guess_cache",
    "search_cache",
    "retriever_cache",
    "robots_cache",
    "extraction_cache",
    "llm_response_cache",
    "entities",
    "entity_aliases",
    "verification_cache",
)


class CachedOnlyNetworkError(RuntimeError):
    """Raised by `--cached-only`'s transport when a live HTTP call is
    attempted — every layer's own Postgres cache should already have made
    this unreachable; reaching here means the cache seed is incomplete, and
    CI should fail loud rather than silently spend real money."""


async def _cached_only_handler(request: httpx.Request) -> httpx.Response:
    raise CachedOnlyNetworkError(
        f"--cached-only: live network call attempted -> {request.method} {request.url}"
    )


def build_runner_http_client(*, cached_only: bool) -> httpx.AsyncClient:
    if cached_only:
        return httpx.AsyncClient(transport=httpx.MockTransport(_cached_only_handler))
    return build_client()


@dataclass(frozen=True)
class QueryScore:
    """One query's scored result — exactly what gets written to
    `bench/results/<date>/<id>.json`. Flat and JSON-native on purpose (no
    nested dataclasses) so a snapshot is readable without importing
    `bench.metrics` to decode it."""

    query_id: str
    query: str
    run_id: str
    difficulty: str
    split: str
    competitor_recall: float
    precision_proxy: float
    fact_accuracy: float
    sentence_binding_rate: float
    contradiction_fired: bool
    cost_usd: float
    llm_cost_usd: float
    search_cost_usd: float
    duration_s: float
    coverage: float
    used_fallback: bool
    claims_dropped: dict[str, int]
    synthesis_omitted_sections: list[str]


def score_outcome(query: BenchmarkQuery, outcome: RunOutcome) -> QueryScore:
    report = outcome.report
    gt = query.ground_truth
    return QueryScore(
        query_id=query.id,
        query=query.query,
        run_id=outcome.run_id,
        difficulty=query.difficulty,
        split=query.split,
        competitor_recall=metrics.competitor_recall(report, gt),
        precision_proxy=metrics.precision_proxy(report, gt),
        fact_accuracy=metrics.fact_accuracy(report, gt),
        sentence_binding_rate=metrics.sentence_binding_rate(report),
        contradiction_fired=metrics.contradiction_fired(report),
        cost_usd=outcome.cost_usd,
        llm_cost_usd=outcome.llm_cost_usd,
        search_cost_usd=outcome.search_cost_usd,
        duration_s=outcome.duration_s,
        coverage=outcome.coverage,
        used_fallback=outcome.used_fallback,
        claims_dropped=dict(outcome.stats.claims_dropped),
        synthesis_omitted_sections=metrics.synthesis_omitted_sections(report),
    )


async def run_and_score(
    pool: asyncpg.Pool, http: httpx.AsyncClient, settings: Settings, query: BenchmarkQuery
) -> QueryScore:
    logger.info("bench.query_started", query_id=query.id, query=query.query)
    outcome = await run_query(pool, http, settings, query.query, is_benchmark=True)
    score = score_outcome(query, outcome)
    logger.info(
        "bench.query_scored",
        query_id=query.id,
        recall=score.competitor_recall,
        precision=score.precision_proxy,
        fact_accuracy=score.fact_accuracy,
        sentence_binding_rate=score.sentence_binding_rate,
        cost_usd=score.cost_usd,
    )
    return score


async def run_all(
    pool: asyncpg.Pool, http: httpx.AsyncClient, settings: Settings, queries: list[BenchmarkQuery]
) -> list[QueryScore]:
    """Sequential, not `asyncio.gather` — a benchmark run's own numbers
    (latency especially) should reflect one query at a time competing for
    nothing else, and ten queries at a few cents/seconds each is cheap
    enough that parallelizing buys nothing worth the added noise."""
    return [await run_and_score(pool, http, settings, q) for q in queries]


def write_results(
    scores: list[QueryScore], *, results_dir: Path = RESULTS_DIR, as_of: date | None = None
) -> Path:
    day = as_of or date.today()
    out_dir = results_dir / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    for score in scores:
        payload = json.dumps(asdict(score), indent=2, sort_keys=True) + "\n"
        (out_dir / f"{score.query_id}.json").write_text(payload)
    return out_dir


def print_summary(scores: list[QueryScore]) -> None:
    for s in scores:
        print(
            f"  {s.query_id}: recall={s.competitor_recall:.2f} precision={s.precision_proxy:.2f} "
            f"fact_accuracy={s.fact_accuracy:.2f} binding={s.sentence_binding_rate:.2f} "
            f"contradiction={s.contradiction_fired} cost=${s.cost_usd:.4f} "
            f"duration={s.duration_s:.1f}s coverage={s.coverage:.2f} "
            f"fallback={s.used_fallback}"
        )


def export_cache_seed(
    database_url: str,
    *,
    out_path: Path = FIXTURES_DIR / "cache_seed.sql",
    pg_dump_argv: list[str] | None = None,
) -> Path:
    """`pg_dump --data-only`, scoped to the tables a cached rerun of the
    benchmark queries actually reads. Dumps the *current* state of those
    tables in `database_url`'s database — if that database has run
    anything besides this benchmark, the seed carries those rows along too
    (harmless for `--cached-only` replay, since an unrelated cached row is
    simply never looked up; just not minimal). A dedicated benchmark
    database would be minimal instead, at the cost of a second live run to
    populate it — not done this session, see `docs/tuning.md`.

    `pg_dump_argv` defaults to a bare local `pg_dump`; this project's own
    Postgres runs in Docker (`docker-compose.yml`), and a `pg_dump` client
    older than the server (a real, hit-in-this-session mismatch: this
    machine's system `pg_dump` is 14.x against this project's Postgres 16)
    refuses to run at all — pass `["docker", "compose", "exec", "-T",
    "postgres", "pg_dump"]` to dump via the container's own matching-version
    binary instead, which is what actually produced `cache_seed.sql`.
    `bench.yml` restores the result into CI's own ephemeral Postgres before
    running `--cached-only`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = pg_dump_argv or ["pg_dump"]
    args = [*prefix, "--data-only", "--no-owner", "--no-privileges", database_url]
    for table in CACHE_SEED_TABLES:
        args += ["--table", table]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    out_path.write_text(result.stdout)
    return out_path


async def main_async(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]

    if args.export_cache_seed:
        pg_dump_argv = (
            ["docker", "compose", "exec", "-T", "postgres", "pg_dump"]
            if args.pg_dump_via_docker_compose
            else None
        )
        path = export_cache_seed(str(settings.database_url), pg_dump_argv=pg_dump_argv)
        print(f"wrote cache seed to {path}")
        return 0

    pool = await create_pool(settings)
    http = build_runner_http_client(cached_only=args.cached_only)
    try:
        if args.query:
            queries = [q for q in load_all_queries() if q.id == args.query]
            if not queries:
                print(f"no such query: {args.query}")
                return 1
        elif args.held_out:
            queries = load_held_out_queries(confirm=args.confirm)
        else:
            queries = load_tuning_queries()

        scores = await run_all(pool, http, settings, queries)
        out_dir = write_results(scores)
        print(f"wrote {len(scores)} result(s) to {out_dir}")
        print_summary(scores)
        return 0
    finally:
        await http.aclose()
        await pool.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bench.runner")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--tuning", action="store_true", help="run the six tuning queries (default)")
    group.add_argument("--held-out", action="store_true", help="run the four held-out queries")
    group.add_argument("--query", type=str, default=None, help="run a single query by id, e.g. q05")
    group.add_argument(
        "--export-cache-seed",
        action="store_true",
        help="pg_dump the cache tables to bench/fixtures/cache_seed.sql and exit",
    )
    parser.add_argument(
        "--pg-dump-via-docker-compose",
        action="store_true",
        help=(
            "run pg_dump inside `docker compose exec postgres` instead of a local binary "
            "(use when the local pg_dump's version is older than the server's, e.g. Postgres 16)"
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required with --held-out (masterplan §10 discipline)",
    )
    parser.add_argument(
        "--cached-only",
        action="store_true",
        help="refuse any live network call; used by CI, proves the cache seed is complete",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
