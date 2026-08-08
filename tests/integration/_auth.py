"""Shared Phase 12 auth test helpers: a throwaway EC keypair, a fake
Supabase JWKS response, and a JWT signer — so integration tests exercise
real JWKS-based verification (`api.web.auth`) without a real Supabase
project, the same "scripted, not a real vendor" spirit as
`tests/integration/_http.py`'s `ScriptedTransport`.
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
KID = "test-key-1"
ISSUER = "https://test.supabase.co/auth/v1"
AUDIENCE = "authenticated"
JWKS_PATH = "/auth/v1/.well-known/jwks.json"


def _b64(value: int, length: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


def jwks_response() -> dict[str, Any]:
    numbers = _PRIVATE_KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "kid": KID,
                "alg": "ES256",
                "use": "sig",
                "x": _b64(numbers.x, 32),
                "y": _b64(numbers.y, 32),
            }
        ]
    }


def sign_jwt(
    *,
    sub: str,
    email: str | None = "user@example.com",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: int = 3600,
    kid: str | None = KID,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": email,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
    }
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="ES256", headers=headers)


def new_user_id() -> str:
    return str(uuid.uuid4())
