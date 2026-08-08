"""FastAPI application (phase doc's Deliverables `app.py`): routing,
middleware, error handlers. `create_app` takes an already-built pool and
HTTP client rather than owning their lifecycle itself — `api.web.main` (the
production entrypoint) builds and tears them down around `uvicorn.serve`;
tests build a real `pg_pool` fixture and a scripted `httpx.AsyncClient`, the
same `httpx.MockTransport` convention `tests/integration/_http.py` already
established for the retrieval layer. No FastAPI `lifespan=` hook is used, so
there is exactly one place (whichever caller built the resources) that owns
closing them — no ownership-tracking needed here.
"""

from __future__ import annotations

import asyncio

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from api.config import Settings
from api.web.auth import AuthContext, JWKSCache
from api.web.errors import (
    APIError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from api.web.quota import ConcurrencyQueue
from api.web.routes import health, reports, runs


def create_app(settings: Settings, pool: asyncpg.Pool, http: httpx.AsyncClient) -> FastAPI:
    app = FastAPI(title="AI Product Investigator API")

    app.state.settings = settings
    app.state.pool = pool
    app.state.http = http
    app.state.concurrency_queue = ConcurrencyQueue(settings.max_concurrent_runs)
    app.state.background_tasks = set[asyncio.Task[None]]()

    # Unconfigured Supabase (`supabase_url` unset, e.g. bare local dev) gets
    # an `AuthContext` whose JWKS URL is empty — every JWT then fails to
    # verify (no key can ever resolve), a safe default rather than a crash
    # at import time or a silently-open API.
    jwks_url = (
        f"{settings.supabase_url}/auth/v1/.well-known/jwks.json" if settings.supabase_url else ""
    )
    issuer = f"{settings.supabase_url}/auth/v1" if settings.supabase_url else ""
    app.state.auth_ctx = AuthContext(
        jwks=JWKSCache(http, jwks_url), issuer=issuer, audience=settings.supabase_jwt_audience
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(reports.router)
    return app


__all__ = ["create_app"]
