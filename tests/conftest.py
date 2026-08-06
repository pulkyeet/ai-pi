from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ai_pi_test"
)


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def postgres_available(postgres_dsn: str) -> bool:
    async def _check() -> bool:
        try:
            conn = await asyncpg.connect(dsn=postgres_dsn, timeout=2)
        except (OSError, asyncpg.PostgresError):
            return False
        await conn.close()
        return True

    return asyncio.run(_check())


@pytest.fixture
def skip_without_postgres(postgres_available: bool) -> None:
    if not postgres_available:
        pytest.skip(
            f"no Postgres reachable at {TEST_DATABASE_URL}; "
            "run `make db-up` (needs Docker) to enable integration tests"
        )


@pytest.fixture
async def pg_pool(postgres_dsn: str, skip_without_postgres: None) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(dsn=postgres_dsn)
    try:
        yield pool
    finally:
        await pool.close()
