"""Phase 01 spike: static crawl viability (httpx + trafilatura) across 40 real
pricing pages, plus the path-guessing hit-rate measurement that reuses the
same corpus.

Answers masterplan §14 open item #3's first half: what fraction of real
pricing pages does a JS-free fetch + extraction pipeline actually recover?

Run: uv run python spikes/crawl_static.py
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import trafilatura
from _common import hr
from pricing_corpus import CORPUS, GUESS_PATHS

PAGES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pages"
RESULTS_PATH = Path(__file__).resolve().parent / "_static_results.json"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

PRICE_RE = re.compile(
    r"[$€£¥₹]\s?\d{1,6}(,\d{3})*(\.\d{2})?"  # $12, ₹1,670, €12.99 — vendors localize by request geo
    r"|(?:USD|EUR|GBP|INR)\s?\d{1,6}"
    r"|\bfree\b.{0,20}\bplan\b",
    re.IGNORECASE,
)


def has_price(text: str) -> bool:
    return bool(PRICE_RE.search(text))


async def fetch(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        return await client.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=15)
    except httpx.HTTPError:
        return None


async def crawl_known_pages(client: httpx.AsyncClient) -> list[dict]:
    sem = asyncio.Semaphore(8)
    results: list[dict] = []

    async def one(page) -> None:
        async with sem:
            url = f"https://{page.domain}{page.known_path}"
            resp = await fetch(client, url)
            entry = {
                "domain": page.domain,
                "category": page.category,
                "url": url,
                "status": resp.status_code if resp is not None else None,
                "static_hit": False,
                "extracted_chars": 0,
            }
            if resp is not None and resp.status_code == 200:
                (PAGES_DIR / f"{page.domain.replace('.', '_')}.html").write_text(
                    resp.text, encoding="utf-8", errors="replace"
                )
                extracted = trafilatura.extract(resp.text) or ""
                entry["extracted_chars"] = len(extracted)
                entry["static_hit"] = has_price(extracted) or has_price(resp.text)
            results.append(entry)

    await asyncio.gather(*(one(p) for p in CORPUS))
    return results


async def path_guess_hit_rate(client: httpx.AsyncClient) -> dict[str, list[str]]:
    sem = asyncio.Semaphore(8)
    hits_by_domain: dict[str, list[str]] = {}

    async def one(domain: str, path: str) -> None:
        async with sem:
            resp = await fetch(client, f"https://{domain}{path}")
            if resp is not None and resp.status_code == 200:
                text = resp.text
                if has_price(trafilatura.extract(text) or "") or has_price(text):
                    hits_by_domain.setdefault(domain, []).append(path)

    await asyncio.gather(*(one(p.domain, path) for p in CORPUS for path in GUESS_PATHS))
    return hits_by_domain


async def main() -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient() as client:
        hr("Static crawl (httpx + trafilatura) over 40 known pricing URLs")
        known_results = await crawl_known_pages(client)
        hits = sum(1 for r in known_results if r["static_hit"])
        print(f"static hit rate: {hits}/{len(known_results)} = {hits / len(known_results):.0%}")
        for r in sorted(known_results, key=lambda r: r["static_hit"]):
            flag = "OK " if r["static_hit"] else "MISS"
            print(f"  [{flag}] {r['domain']:<20} status={r['status']} extracted_chars={r['extracted_chars']}")

        hr("Path-guessing hit rate (/pricing, /plans, /pricing-plans against root domain)")
        guess_hits = await path_guess_hit_rate(client)
        n_guess_hit = sum(1 for p in CORPUS if p.domain in guess_hits)
        print(f"guess hit rate: {n_guess_hit}/{len(CORPUS)} = {n_guess_hit / len(CORPUS):.0%}")
        for p in CORPUS:
            paths = guess_hits.get(p.domain, [])
            flag = "OK " if paths else "MISS"
            print(f"  [{flag}] {p.domain:<20} known={p.known_path:<28} guess_hits={paths}")

    RESULTS_PATH.write_text(
        json.dumps(
            {
                "known_page_results": known_results,
                "guess_hits_by_domain": guess_hits,
            },
            indent=2,
        )
    )
    print(f"\nwrote {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
