"""Cloudflare Turnstile (masterplan §8.3's bot filter, gated ahead of any
spend — see `api.web.routes.runs`'s ordering). Unconfigured
(`turnstile_secret_key` unset) is a no-op that always passes, the same
"`None` means unconfigured, never a crash" convention as every other
optional credential in `api.config.Settings`.
"""

from __future__ import annotations

import httpx

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify(
    http: httpx.AsyncClient,
    *,
    secret_key: str | None,
    token: str | None,
    remote_ip: str | None = None,
) -> bool:
    if secret_key is None:
        return True
    if not token:
        return False
    data = {"secret": secret_key, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    response = await http.post(SITEVERIFY_URL, data=data, timeout=5.0)
    response.raise_for_status()
    result: dict[str, object] = response.json()
    return bool(result.get("success"))


__all__ = ["verify"]
