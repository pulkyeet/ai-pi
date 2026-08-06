from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class CacheOutcome(StrEnum):
    """Per-fetch accounting, aggregated into `meta.cache_hit_rate` in the report
    contract (Phase 11). HIT never touches the network; MISS always did (a fresh
    fetch, including a fresh non-200); CONDITIONAL_304 touched the network but
    transferred nothing."""

    HIT = "hit"
    MISS = "miss"
    CONDITIONAL_304 = "conditional-304"


class Source(BaseModel):
    """Mirrors the `sources` table (masterplan §4.3, Phase 00 migration 0001,
    Phase 03 migration 0003). `extracted_text` is the single canonical,
    normalised copy of a page's text — see `api.retrieval.extract_text` for the
    one-owner rule span binding (Phase 06) depends on."""

    id: int | None = None
    canonical_url: str
    root_key: str | None = None
    fetched_at: datetime | None = None
    http_status: int | None = None
    content_hash: str | None = None
    extracted_text: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    retrieval_reason: str | None = None
    ttl_expires_at: datetime | None = None
    is_pinned: bool = False
