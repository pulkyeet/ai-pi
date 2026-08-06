"""trafilatura wrapper and the single canonical normalisation pass.

The stored text is canonical and immutable: `api.retrieval.fetch` writes it
to `sources.extracted_text` exactly once, and span binding (Phase 06)
computes `claims.char_start`/`char_end` against exactly these bytes.
**Normalise here, once, at write time — nothing downstream may re-normalise
extracted text.** A second normalisation pass invalidates every stored
offset silently. See `tests/unit/test_normalisation.py` for the idempotence
property test that guards this.
"""

from __future__ import annotations

import re
import unicodedata

import trafilatura

_BLANK_RUN_RE = re.compile(r"\n{3,}")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BLANK_RUN_RE.sub("\n\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.rstrip("\n") + "\n"


def extract(html: str, *, url: str | None = None) -> str | None:
    raw = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if not raw:
        return None
    return normalise(raw)
