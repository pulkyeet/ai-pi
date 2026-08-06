"""Scripted `httpx.MockTransport` for offline retrieval-layer tests.

Per the phase doc's own test spec ("asserted by a mock transport that fails
on any request"), fetch-layer integration tests use `httpx.MockTransport`
rather than VCR cassettes: there's no real vendor to record, only our own
HTTP mechanics (retries, redirects, conditional requests, size caps) to
exercise deterministically and offline.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

import httpx

USER_AGENT = "AIProductInvestigatorBot/0.1 (+mailto:pulkyeet@gmail.com)"

Step = httpx.Response | Exception


class ScriptedTransport:
    """`path -> [Step, ...]` — each request against `path` consumes the next
    step in its queue (the last one repeats once exhausted). A step that is
    an `Exception` is raised instead of returned, to simulate a timeout or
    connection failure. Unscripted `/robots.txt` requests 404 (allow-all);
    any other unscripted path 404s too."""

    def __init__(self, script: dict[str, list[Step]] | None = None) -> None:
        self.script: dict[str, list[Step]] = {k: list(v) for k, v in (script or {}).items()}
        self.calls: dict[str, int] = defaultdict(int)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        queue = self.script.get(path)
        if not queue:
            return httpx.Response(404)
        step = queue[0] if len(queue) == 1 else queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_client(transport: ScriptedTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(transport.handler),
        follow_redirects=True,
        max_redirects=5,
        headers={"User-Agent": USER_AGENT},
    )


def unique_root() -> str:
    """A syntactically valid, globally-unique-for-this-test registrable
    domain, so `sources.canonical_url` / `path_guess_cache.root_key` never
    collide with another test or a previous run against the same database."""
    return f"t{uuid.uuid4().hex[:12]}.com"


PRICING_HTML = (
    b"<html><body><main><h1>Pricing</h1><p>Pro plan: $29/mo per seat. "
    b"A free tier is also available for small teams evaluating the product "
    b"before committing to a paid plan.</p></main></body></html>"
)

PLAIN_HTML = (
    b"<html><body><main><h1>Welcome</h1><p>This is a perfectly ordinary "
    b"marketing page with more than two hundred characters of body copy so "
    b"that it clears the thin-content threshold used by the fetch layer "
    b"when deciding whether a short page is a real page or a cookie wall "
    b"styled to look like one.</p></main></body></html>"
)

COOKIE_WALL_HTML = b"<html><body><p>We use cookies. Accept cookies to continue.</p></body></html>"
