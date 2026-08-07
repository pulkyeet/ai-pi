"""Shared input/output types for `api.resolve`, split out from `__init__.py`
purely to avoid a cycle: `alias.py` needs `EntityEvidence`'s shape to detect
merge triggers, and `__init__.py` (the orchestrator) needs both `alias.py`
and this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg
import httpx

from api.models.entity import EntityScheme
from api.resolve.maturity import MaturitySignals
from api.retrieval.fetch import HostThrottle
from api.retrieval.robots import RobotsCache
from api.sources.github import GitHubRetriever
from api.sources.producthunt import ProductHuntRetriever


@dataclass(frozen=True)
class EntityEvidence:
    """What a caller (a future Phase 10 task handler) has already gathered
    for one candidate product before entity resolution runs.

    The three alias-merge triggers (Design section) are evidence facts
    discovered *outside* this package — a repo's `homepage` field, a page's
    footer backlink, a package's `repository` field — `api.resolve.alias`
    only recognises them once supplied. `homepage_url` and `repository_url`
    are also opportunistically filled in for free from `verify.py`'s own
    GitHub/npm/PyPI lookups when the caller doesn't already have them; a
    caller-supplied value always takes precedence over an observed one.
    `None` means "not known", never "confirmed absent".
    """

    scheme: EntityScheme
    raw_value: str
    display_name: str
    homepage_url: str | None = None
    backlink_repo_url: str | None = None
    repository_url: str | None = None
    signals: MaturitySignals = field(default_factory=MaturitySignals)


@dataclass
class VerificationContext:
    """Bundles the clients `api.resolve.verify` needs, one per external
    boundary it touches. `github`/`producthunt` are `None` when a caller
    hasn't configured that vendor — verifying a `gh:`/`ph:` candidate
    without the matching retriever raises `RetrieverUnavailableError`
    rather than silently treating "no client" as "doesn't exist"."""

    pool: asyncpg.Pool
    http: httpx.AsyncClient
    throttle: HostThrottle
    robots: RobotsCache
    github: GitHubRetriever | None = None
    producthunt: ProductHuntRetriever | None = None


__all__ = ["EntityEvidence", "VerificationContext"]
