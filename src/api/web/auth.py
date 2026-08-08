"""Supabase JWT verification (phase doc's "Auth" design).

Login happens in the browser against Supabase; this module only verifies the
resulting JWT. **Verification is local** — a JWKS signature check, not a
network call per request (`JWKSCache` fetches once and refreshes only on a
`kid` miss). Expiry, issuer, and audience are each checked, and each
rejection is independently distinguishable (own `code`) because "we verify
the token" is the kind of claim that is easy to get subtly wrong and never
notice — see the phase doc's own testing table.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import jwt
from fastapi import Depends, Request

from api.web.errors import AuthRequiredError, InvalidTokenError


@dataclass(frozen=True)
class User:
    id: UUID
    email: str | None
    quota_override: int | None
    is_admin: bool


class JWKSCache:
    """Caches Supabase's JWKS keyed by `kid`. A `kid` not currently cached
    triggers exactly one refetch of the whole key set (Supabase rotates keys
    as a set, not individually) — never a per-request fetch."""

    def __init__(self, http: httpx.AsyncClient, jwks_url: str) -> None:
        self._http = http
        self._jwks_url = jwks_url
        self._keys: dict[str, jwt.PyJWK] = {}
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> jwt.PyJWK:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            if kid not in self._keys:
                await self._refresh()
        try:
            return self._keys[kid]
        except KeyError:
            raise InvalidTokenError("unknown signing key", code="unknown_kid") from None

    async def _refresh(self) -> None:
        response = await self._http.get(self._jwks_url, timeout=5.0)
        response.raise_for_status()
        keys: dict[str, jwt.PyJWK] = {}
        for raw in response.json()["keys"]:
            jwk = jwt.PyJWK.from_dict(raw)
            if jwk.key_id is not None:
                keys[jwk.key_id] = jwk
        self._keys = keys


@dataclass
class AuthContext:
    jwks: JWKSCache
    issuer: str
    audience: str


def _decode(token: str, key: jwt.PyJWK, *, issuer: str, audience: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key.key,
            algorithms=[key.algorithm_name] if key.algorithm_name else None,
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "sub"]},
        )
        return claims
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token expired", code="token_expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidTokenError("wrong issuer", code="wrong_issuer") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidTokenError("wrong audience", code="wrong_audience") from exc
    except jwt.InvalidSignatureError as exc:
        raise InvalidTokenError("bad signature", code="bad_signature") from exc
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("malformed token", code="malformed_token") from exc


async def verify_token(token: str, *, ctx: AuthContext) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("malformed token", code="malformed_token") from exc
    kid = header.get("kid")
    if not kid:
        raise InvalidTokenError("token missing kid", code="malformed_token")
    key = await ctx.jwks.get_key(kid)
    return _decode(token, key, issuer=ctx.issuer, audience=ctx.audience)


async def provision_user(pool: asyncpg.Pool, user_id: UUID) -> tuple[int | None, bool]:
    """First login creates `user_profiles`; every later call reuses the same
    row — one round trip either way (`ON CONFLICT DO NOTHING` plus a
    fallback read, rather than a separate exists-check)."""
    row = await pool.fetchrow(
        """
        WITH ins AS (
            INSERT INTO user_profiles (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING quota_override, is_admin
        )
        SELECT quota_override, is_admin FROM ins
        UNION ALL
        SELECT quota_override, is_admin FROM user_profiles WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    assert row is not None
    return row["quota_override"], bool(row["is_admin"])


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    return token or None


async def optional_user(request: Request) -> User | None:
    token = _bearer_token(request)
    if token is None:
        return None
    claims = await verify_token(token, ctx=request.app.state.auth_ctx)
    user_id = UUID(str(claims["sub"]))
    quota_override, is_admin = await provision_user(request.app.state.pool, user_id)
    return User(
        id=user_id,
        email=claims.get("email"),
        quota_override=quota_override,
        is_admin=is_admin,
    )


async def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise AuthRequiredError("authentication required")
    return user


__all__ = [
    "AuthContext",
    "JWKSCache",
    "User",
    "current_user",
    "optional_user",
    "provision_user",
    "verify_token",
]
