"""Shared FastAPI test-app builder for Phase 12 (`api.web`) integration
tests: a real `Settings`, a scripted `httpx.AsyncClient` standing in for
Supabase's JWKS endpoint and Cloudflare's Turnstile siteverify (the only two
external HTTP calls `api.web` itself makes), and a helper to seed the
`auth.users` row a signed JWT's `sub` needs to satisfy `user_profiles`' FK —
mirroring `api.cli.ensure_cli_user`'s own local-stub pattern.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
from _auth import ISSUER, JWKS_PATH, jwks_response

from api.config import Settings

TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/ai_pi_test"


def build_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "openrouter_api_key": "test",
        "exa_api_key": "test",
        "github_token": "test",
        "supabase_url": "https://test.supabase.co",
        "supabase_jwt_audience": "authenticated",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


assert ISSUER.startswith("https://test.supabase.co"), "_auth.ISSUER must match build_settings"


class ScriptedExternalTransport:
    """Routes by exact path — a scripted stand-in for Supabase JWKS and
    Cloudflare Turnstile, never a real vendor call."""

    def __init__(self) -> None:
        self.turnstile_result = True
        self.calls: dict[str, int] = {}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] = self.calls.get(path, 0) + 1
        if path == JWKS_PATH:
            return httpx.Response(200, json=jwks_response())
        if path == "/turnstile/v0/siteverify":
            return httpx.Response(200, json={"success": self.turnstile_result})
        return httpx.Response(404)


def build_http_client() -> tuple[httpx.AsyncClient, ScriptedExternalTransport]:
    transport = ScriptedExternalTransport()
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
    return client, transport


async def seed_auth_user(pool: asyncpg.Pool, user_id: str, email: str | None = None) -> None:
    await pool.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
        uuid.UUID(user_id),
        email or f"{user_id}@example.com",
    )
