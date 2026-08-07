"""A host-*and*-path-routed `httpx.MockTransport`, for Phase 10 handler
tests. `tests/integration/_http.py`'s `ScriptedTransport` routes on path
alone — fine for the fetch layer's own tests (one host per test), but
`api.tasks` handlers routinely need two *different* hosts to respond
differently on the same path within a single test (e.g. `discover.py`
verifying one real candidate domain and one 404 "hallucinated" one, both
fetched at `/`). This is that, plus small response builders for the vendor
shapes handlers actually call: GitHub REST, Exa search, and OpenRouter chat
completions.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import httpx

USER_AGENT = "AIProductInvestigatorBot/0.1 (+mailto:pulkyeet@gmail.com)"

Step = httpx.Response | Exception


class HostRoutedTransport:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Step]] = {}
        self.calls: dict[tuple[str, str], int] = defaultdict(int)

    def add(self, host: str, path: str, *steps: Step) -> None:
        self.routes[(host, path)] = list(steps)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        key = (request.url.host, request.url.path)
        self.calls[key] += 1
        queue = self.routes.get(key)
        if not queue:
            return httpx.Response(404)
        step = queue[0] if len(queue) == 1 else queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_client(transport: HostRoutedTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(transport.handler),
        follow_redirects=True,
        max_redirects=5,
        headers={"User-Agent": USER_AGENT},
    )


def html_page(body_text: str) -> httpx.Response:
    html = f"<html><body><main><h1>Page</h1><p>{body_text}</p></main></body></html>"
    return httpx.Response(200, content=html.encode(), headers={"content-type": "text/html"})


def exa_response(results: list[dict[str, Any]], *, credits_usd: float = 0.007) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "url": r["url"],
                    "title": r.get("title", ""),
                    "text": r.get("text", ""),
                }
                for r in results
            ],
            "costDollars": {"total": credits_usd},
        },
    )


def chat_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 60,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


def extraction_response(claims: list[dict[str, Any]]) -> httpx.Response:
    return chat_response({"claims": claims})


def github_repo_response(
    *,
    full_name: str,
    stars: int = 100,
    open_issues: int = 5,
    license_spdx: str | None = "MIT",
    pushed_at: str = "2026-06-01T00:00:00Z",
    homepage: str | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "full_name": full_name,
            "stargazers_count": stars,
            "open_issues_count": open_issues,
            "license": {"spdx_id": license_spdx} if license_spdx else None,
            "pushed_at": pushed_at,
            "homepage": homepage,
        },
    )


__all__ = [
    "USER_AGENT",
    "HostRoutedTransport",
    "chat_response",
    "exa_response",
    "extraction_response",
    "github_repo_response",
    "html_page",
    "make_client",
]
