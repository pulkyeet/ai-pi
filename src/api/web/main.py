"""Production entrypoint: `python -m api.web.main`. Builds and owns the pool
and HTTP client for the process lifetime — the one place that creates them
outside a test fixture, mirroring `api.cli`'s own explicit create/close
pattern rather than a FastAPI `lifespan=` hook (see `api.web.app`'s module
docstring for why).
"""

from __future__ import annotations

import asyncio

import uvicorn

from api.config import Settings
from api.db import create_pool
from api.logging import configure_logging, configure_tracing
from api.retrieval import build_client
from api.web.app import create_app


async def _amain() -> None:
    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings)
    configure_tracing(settings)

    pool = await create_pool(settings)
    http = build_client()
    app = create_app(settings, pool, http)
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_config=None)  # noqa: S104
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await http.aclose()
        await pool.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
