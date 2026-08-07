"""Alias detection and merge-graph computation (masterplan §4.5, phase doc's
Design section).

Two halves, deliberately split for testability:

- `detect_alias_edges` recognises the phase doc's three evidence-based merge
  triggers from one candidate's `EntityEvidence`. It never fetches anything
  itself — the facts (a repo's `homepage`, a page's footer backlink, a
  package's `repository` field) are gathered elsewhere and handed in.
- `build_alias_graph` is a pure union-find over a batch of `AliasEdge`s, with
  no I/O at all. It exists so the phase doc's order-independence property
  ("resolving a candidate set in any permutation yields the same entity
  graph") is testable directly with Hypothesis, without spinning up
  Postgres for every example — `api.resolve.store.merge_alias` is the
  DB-effecting counterpart that `api.resolve` (the orchestrator) calls once
  per edge as candidates are resolved one at a time.

Canonical selection is a pure function of scheme precedence
(`api.resolve.entity_key.SCHEME_PRECEDENCE`), never of arrival order — that
is what makes the whole thing order-independent: it doesn't matter which of
two equivalent keys is discovered first, the same one always wins.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from api.models.entity import EntityKey, EntityScheme
from api.resolve import entity_key
from api.resolve.entity_key import precedence_rank
from api.resolve.types import EntityEvidence

# Trigger labels, matching the phase doc's Design section list verbatim.
GH_HOMEPAGE = "gh_homepage"
WEB_BACKLINK = "web_backlink"
PACKAGE_REPOSITORY = "package_repository"


@dataclass(frozen=True)
class AliasEdge:
    a: EntityKey
    b: EntityKey
    trigger: str


def canonical_pair(a: EntityKey, b: EntityKey) -> tuple[EntityKey, EntityKey]:
    """`(canonical, alias)` ordered by scheme precedence. Ties (same scheme,
    which cannot happen for two genuinely different keys under any of the
    three triggers, but kept total for safety) break on `value` so the
    function is a consistent total order regardless of argument order —
    the property that makes merging order-independent."""
    a_rank, b_rank = precedence_rank(a.scheme), precedence_rank(b.scheme)
    if (a_rank, a.value) <= (b_rank, b.value):
        return a, b
    return b, a


def detect_alias_edges(key: EntityKey, evidence: EntityEvidence) -> list[AliasEdge]:
    """The three Design-section triggers, keyed off which scheme `key` is —
    each trigger only applies to specific scheme pairs, so at most one
    fires per candidate."""
    edges: list[AliasEdge] = []

    if key.scheme is EntityScheme.GH and evidence.homepage_url:
        try:
            web_key = entity_key.derive_web_key(evidence.homepage_url)
        except ValueError:
            pass
        else:
            edges.append(AliasEdge(key, web_key, GH_HOMEPAGE))

    if key.scheme is EntityScheme.WEB and evidence.backlink_repo_url:
        try:
            gh_key = entity_key.derive_gh_key(evidence.backlink_repo_url)
        except ValueError:
            pass
        else:
            edges.append(AliasEdge(key, gh_key, WEB_BACKLINK))

    if key.scheme in (EntityScheme.NPM, EntityScheme.PYPI) and evidence.repository_url:
        try:
            gh_key = entity_key.derive_gh_key(evidence.repository_url)
        except ValueError:
            pass
        else:
            edges.append(AliasEdge(key, gh_key, PACKAGE_REPOSITORY))

    return edges


def _find(parent: dict[EntityKey, EntityKey], key: EntityKey) -> EntityKey:
    while parent.get(key, key) != key:
        key = parent[key]
    return key


def build_alias_graph(edges: Iterable[AliasEdge]) -> dict[EntityKey, EntityKey]:
    """`{key: canonical_key}` for every key mentioned by `edges`. A pure
    union-find: processing the same edge set in any order yields the same
    mapping, because `canonical_pair` — not arrival order — decides which
    root wins each union."""
    parent: dict[EntityKey, EntityKey] = {}
    for edge in edges:
        parent.setdefault(edge.a, edge.a)
        parent.setdefault(edge.b, edge.b)
        root_a, root_b = _find(parent, edge.a), _find(parent, edge.b)
        if root_a == root_b:
            continue
        canonical, alias = canonical_pair(root_a, root_b)
        parent[alias] = canonical
    return {key: _find(parent, key) for key in parent}


__all__ = [
    "GH_HOMEPAGE",
    "PACKAGE_REPOSITORY",
    "WEB_BACKLINK",
    "AliasEdge",
    "build_alias_graph",
    "canonical_pair",
    "detect_alias_edges",
]
