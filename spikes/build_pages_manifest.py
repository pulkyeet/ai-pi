"""Build tests/fixtures/pages/manifest.json — the "expected extraction" sidecar
the phase doc's deliverables list calls for, paired with the raw *.html files
crawl_static.py already saved. Runs entirely offline against the saved HTML.

Run: uv run python spikes/build_pages_manifest.py
"""

from __future__ import annotations

import json
from pathlib import Path

import trafilatura
from crawl_static import PRICE_RE
from pricing_corpus import CORPUS

PAGES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pages"


def main() -> None:
    manifest = []
    for page in CORPUS:
        html_path = PAGES_DIR / f"{page.domain.replace('.', '_')}.html"
        if not html_path.exists():
            manifest.append(
                {"domain": page.domain, "category": page.category, "fetched": False, "has_price": False}
            )
            continue
        html = html_path.read_text(encoding="utf-8", errors="replace")
        extracted = trafilatura.extract(html) or ""
        match = PRICE_RE.search(extracted) or PRICE_RE.search(html)
        manifest.append(
            {
                "domain": page.domain,
                "category": page.category,
                "known_path": page.known_path,
                "fetched": True,
                "extracted_chars": len(extracted),
                "has_price": bool(match),
                "matched_price_snippet": match.group(0) if match else None,
            }
        )
    out_path = PAGES_DIR / "manifest.json"
    out_path.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out_path} ({len(manifest)} entries, {sum(1 for m in manifest if m['has_price'])} with a detected price)")


if __name__ == "__main__":
    main()
