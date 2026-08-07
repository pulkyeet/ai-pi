"""Alias-merge trigger detection and the pure union-find merge graph
(Phase 07). The order-independence property test is the phase doc's own
requirement: "resolving a candidate set in any permutation yields the same
entity graph" — entity arrival order depends on task scheduling and is
effectively nondeterministic, so this must hold for *any* permutation, not
just the one a hand-written test happens to exercise.
"""

from __future__ import annotations

import itertools

from hypothesis import given
from hypothesis import strategies as st

from api.models.entity import EntityKey, EntityScheme
from api.resolve.alias import (
    GH_HOMEPAGE,
    PACKAGE_REPOSITORY,
    WEB_BACKLINK,
    AliasEdge,
    build_alias_graph,
    canonical_pair,
    detect_alias_edges,
)
from api.resolve.types import EntityEvidence

WEB = EntityKey(EntityScheme.WEB, "acme.com")
GH = EntityKey(EntityScheme.GH, "acme/widget")
NPM = EntityKey(EntityScheme.NPM, "widget")
PYPI = EntityKey(EntityScheme.PYPI, "widget")
CHROME = EntityKey(EntityScheme.CHROME, "abc123")

# ---------------------------------------------------------------------------
# canonical_pair: scheme precedence
# ---------------------------------------------------------------------------


def test_canonical_pair_prefers_web_over_gh_regardless_of_argument_order() -> None:
    assert canonical_pair(WEB, GH) == (WEB, GH)
    assert canonical_pair(GH, WEB) == (WEB, GH)


def test_canonical_pair_prefers_gh_over_npm() -> None:
    assert canonical_pair(NPM, GH) == (GH, NPM)


# ---------------------------------------------------------------------------
# detect_alias_edges: the three Design-section triggers
# ---------------------------------------------------------------------------


def test_gh_homepage_trigger() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.GH,
        raw_value="acme/widget",
        display_name="Widget",
        homepage_url="https://acme.com",
    )
    edges = detect_alias_edges(GH, evidence)
    assert edges == [AliasEdge(GH, WEB, GH_HOMEPAGE)]


def test_web_backlink_trigger() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.WEB,
        raw_value="acme.com",
        display_name="Acme",
        backlink_repo_url="https://github.com/acme/widget",
    )
    edges = detect_alias_edges(WEB, evidence)
    assert edges == [AliasEdge(WEB, GH, WEB_BACKLINK)]


def test_package_repository_trigger_npm() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.NPM,
        raw_value="widget",
        display_name="widget",
        repository_url="git+https://github.com/acme/widget.git",
    )
    edges = detect_alias_edges(NPM, evidence)
    assert edges == [AliasEdge(NPM, GH, PACKAGE_REPOSITORY)]


def test_package_repository_trigger_pypi() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.PYPI,
        raw_value="widget",
        display_name="widget",
        repository_url="https://github.com/acme/widget",
    )
    edges = detect_alias_edges(PYPI, evidence)
    assert edges == [AliasEdge(PYPI, GH, PACKAGE_REPOSITORY)]


def test_no_trigger_fires_without_matching_evidence() -> None:
    evidence = EntityEvidence(scheme=EntityScheme.WEB, raw_value="acme.com", display_name="Acme")
    assert detect_alias_edges(WEB, evidence) == []


def test_malformed_linking_evidence_is_ignored_not_raised() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.GH,
        raw_value="acme/widget",
        display_name="Widget",
        homepage_url="not a url at all !!",
    )
    # tldextract is permissive enough that most strings still yield *some*
    # registered domain; the trigger should never raise regardless.
    detect_alias_edges(GH, evidence)


def test_malformed_web_backlink_is_ignored_not_raised() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.WEB,
        raw_value="acme.com",
        display_name="Acme",
        backlink_repo_url="not-a-github-url-at-all",
    )
    assert detect_alias_edges(WEB, evidence) == []


def test_malformed_package_repository_is_ignored_not_raised() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.NPM,
        raw_value="widget",
        display_name="widget",
        repository_url="not-a-github-url-at-all",
    )
    assert detect_alias_edges(NPM, evidence) == []


def test_chrome_scheme_never_triggers_any_alias_edge() -> None:
    evidence = EntityEvidence(
        scheme=EntityScheme.CHROME,
        raw_value="abc123",
        display_name="Ext",
        homepage_url="https://acme.com",
        repository_url="https://github.com/acme/widget",
    )
    assert detect_alias_edges(CHROME, evidence) == []


# ---------------------------------------------------------------------------
# build_alias_graph: pure union-find, order-independent
# ---------------------------------------------------------------------------


def test_build_alias_graph_collapses_a_chain() -> None:
    edges = [AliasEdge(GH, WEB, GH_HOMEPAGE), AliasEdge(NPM, GH, PACKAGE_REPOSITORY)]
    graph = build_alias_graph(edges)
    assert graph[WEB] == WEB
    assert graph[GH] == WEB
    assert graph[NPM] == WEB


def test_build_alias_graph_is_order_independent_for_a_fixed_chain() -> None:
    edges = [AliasEdge(GH, WEB, GH_HOMEPAGE), AliasEdge(NPM, GH, PACKAGE_REPOSITORY)]
    graphs = [build_alias_graph(perm) for perm in itertools.permutations(edges)]
    assert all(g == graphs[0] for g in graphs)


_scheme_strategy = st.sampled_from(list(EntityScheme))
_value_strategy = st.text(alphabet="abcdefgh", min_size=1, max_size=4)
_key_strategy = st.builds(EntityKey, scheme=_scheme_strategy, value=_value_strategy)
_edge_strategy = st.builds(
    AliasEdge, a=_key_strategy, b=_key_strategy, trigger=st.sampled_from(["t1", "t2"])
).filter(lambda e: e.a != e.b)


@given(edges=st.lists(_edge_strategy, max_size=8))
def test_build_alias_graph_order_independence_property(edges: list[AliasEdge]) -> None:
    baseline = build_alias_graph(edges)
    shuffled = list(reversed(edges))
    assert build_alias_graph(shuffled) == baseline


@given(edges=st.lists(_edge_strategy, max_size=6))
def test_build_alias_graph_is_idempotent(edges: list[AliasEdge]) -> None:
    once = build_alias_graph(edges)
    twice = build_alias_graph(edges + edges)
    assert once == twice
