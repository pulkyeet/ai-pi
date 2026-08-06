"""Phase 01 spike: Playwright recovery on the pages crawl_static.py failed to
extract a price from. Second half of masterplan §14 open item #3.

Also records the cost side of the decision: browser download size and
cold-start latency, since "Playwright ships" means a heavier deploy image.

Run: uv run python spikes/crawl_static.py first, then:
     uv run python spikes/crawl_js.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import trafilatura
from _common import hr
from crawl_static import has_price
from playwright.sync_api import sync_playwright

RESULTS_PATH = Path(__file__).resolve().parent / "_static_results.json"


def main() -> None:
    if not RESULTS_PATH.exists():
        raise SystemExit("run crawl_static.py first to produce _static_results.json")

    data = json.loads(RESULTS_PATH.read_text())
    failures = [r for r in data["known_page_results"] if not r["static_hit"]]

    hr(f"Playwright recovery attempt on {len(failures)} static failures")

    with sync_playwright() as p:
        cold_start = time.perf_counter()
        browser = p.chromium.launch()
        cold_start_ms = (time.perf_counter() - cold_start) * 1000
        print(f"cold start (launch to ready): {cold_start_ms:.0f}ms")

        recovered = 0
        for entry in failures:
            page = browser.new_page()
            try:
                start = time.perf_counter()
                page.goto(entry["url"], timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)  # let client-side rendering settle
                content = page.content()
                elapsed_ms = (time.perf_counter() - start) * 1000
                extracted = trafilatura.extract(content) or ""
                fixed = has_price(extracted) or has_price(content)
                recovered += fixed
                print(f"  [{'FIXED' if fixed else 'still miss'}] {entry['domain']:<20} render_time={elapsed_ms:.0f}ms")
            except Exception as exc:  # noqa: BLE001 — spike script, log and continue
                print(f"  [ERROR] {entry['domain']:<20} {type(exc).__name__}: {exc}")
            finally:
                page.close()

        browser.close()

    print(
        f"\nPlaywright recovery: {recovered}/{len(failures)} = "
        f"{recovered / len(failures):.0%} of static failures fixed"
        if failures
        else "no failures to recover"
    )


if __name__ == "__main__":
    main()
