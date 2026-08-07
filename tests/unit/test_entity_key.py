"""Entity key derivation (Phase 07). The PSL table test is this phase's
signature unit test — see the phase doc, Testing table: "one assertion per
PaaS host... because it is a single constructor flag standing between
correct behaviour and a report that says '43 competitors' when it means '3
competitors and 40 weekend projects'."
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.models.entity import EntityKey, EntityScheme
from api.resolve.entity_key import (
    SCHEME_PRECEDENCE,
    derive_chrome_key,
    derive_gh_key,
    derive_hf_key,
    derive_ios_key,
    derive_key,
    derive_npm_key,
    derive_ph_key,
    derive_pypi_key,
    derive_web_key,
    is_paas_host,
    precedence_rank,
)

# ---------------------------------------------------------------------------
# The .fly.dev signature test: >=10 PaaS hosts, each a distinct entity, none
# collapsing to the bare platform domain.
# ---------------------------------------------------------------------------

PAAS_HOSTS = [
    "ai-expense-reporter.fly.dev",
    "my-startup.vercel.app",
    "widget-tool.netlify.app",
    "someuser.github.io",
    "my-api.herokuapp.com",
    "docs-site.pages.dev",
    "backend.up.railway.app",
    "service.onrender.com",
    "worker.workers.dev",
    "pwa-app.web.app",
    "console.firebaseapp.com",
]


@pytest.mark.parametrize("host", PAAS_HOSTS)
def test_paas_host_keeps_full_tenant_subdomain(host: str) -> None:
    key = derive_web_key(host)
    assert key.value == host.lower()
    assert key.scheme is EntityScheme.WEB


def test_paas_hosts_all_yield_distinct_keys() -> None:
    keys = {derive_web_key(host) for host in PAAS_HOSTS}
    assert len(keys) == len(PAAS_HOSTS)


def test_paas_hosts_never_collapse_to_bare_platform_domain() -> None:
    bare_platform_domains = {
        "fly.dev",
        "vercel.app",
        "netlify.app",
        "github.io",
        "herokuapp.com",
        "pages.dev",
        "railway.app",
        "onrender.com",
        "workers.dev",
        "web.app",
        "firebaseapp.com",
    }
    for host in PAAS_HOSTS:
        assert derive_web_key(host).value not in bare_platform_domains


@pytest.mark.parametrize("host", PAAS_HOSTS)
def test_is_paas_host_true_for_every_paas_host(host: str) -> None:
    assert is_paas_host(host) is True


def test_is_paas_host_false_for_a_normal_domain() -> None:
    assert is_paas_host("acme.com") is False
    assert is_paas_host("www.acme.com") is False


# ---------------------------------------------------------------------------
# web: derivation
# ---------------------------------------------------------------------------


def test_web_key_strips_www() -> None:
    assert derive_web_key("https://www.acme.com/pricing") == EntityKey(EntityScheme.WEB, "acme.com")


def test_web_key_lowercases_host() -> None:
    assert derive_web_key("https://ACME.COM") == EntityKey(EntityScheme.WEB, "acme.com")


def test_web_key_ignores_path_and_query() -> None:
    a = derive_web_key("https://acme.com/pricing?ref=hn")
    b = derive_web_key("https://acme.com")
    assert a == b


# ---------------------------------------------------------------------------
# gh: derivation
# ---------------------------------------------------------------------------


def test_gh_key_from_bare_owner_repo() -> None:
    assert derive_gh_key("Acme/Widget") == EntityKey(EntityScheme.GH, "acme/widget")


def test_gh_key_from_full_url_strips_git_and_trailing_slash() -> None:
    assert derive_gh_key("https://github.com/Acme/Widget.git/") == EntityKey(
        EntityScheme.GH, "acme/widget"
    )


def test_gh_key_from_url_without_scheme() -> None:
    assert derive_gh_key("github.com/acme/widget") == EntityKey(EntityScheme.GH, "acme/widget")


def test_gh_key_from_npm_repository_field_git_plus_https() -> None:
    assert derive_gh_key("git+https://github.com/acme/widget.git") == EntityKey(
        EntityScheme.GH, "acme/widget"
    )


def test_gh_key_from_scp_style_ssh() -> None:
    assert derive_gh_key("git@github.com:acme/widget.git") == EntityKey(
        EntityScheme.GH, "acme/widget"
    )


def test_gh_key_rejects_malformed_input() -> None:
    with pytest.raises(ValueError, match="cannot derive gh:"):
        derive_gh_key("not-a-repo-path/with/too/many/segments")


# ---------------------------------------------------------------------------
# pypi: PEP 503 normalisation
# ---------------------------------------------------------------------------


def test_pypi_key_pep503_normalises() -> None:
    assert derive_pypi_key("Foo.Bar_Baz") == EntityKey(EntityScheme.PYPI, "foo-bar-baz")


def test_pypi_key_collapses_runs_of_separators() -> None:
    assert derive_pypi_key("foo__..--bar") == EntityKey(EntityScheme.PYPI, "foo-bar")


def test_npm_key_lowercases_and_preserves_scope() -> None:
    assert derive_npm_key("@Acme/Widget") == EntityKey(EntityScheme.NPM, "@acme/widget")


def test_hf_key_lowercases() -> None:
    assert derive_hf_key("Acme/my-space") == EntityKey(EntityScheme.HF, "acme/my-space")


def test_chrome_key_lowercases() -> None:
    assert derive_chrome_key("ABC123") == EntityKey(EntityScheme.CHROME, "abc123")


def test_ios_key_lowercases() -> None:
    assert derive_ios_key(" 123456 ") == EntityKey(EntityScheme.IOS, "123456")


def test_ph_key_lowercases() -> None:
    assert derive_ph_key("My-Product") == EntityKey(EntityScheme.PH, "my-product")


@pytest.mark.parametrize(
    "deriver",
    [
        derive_gh_key,
        derive_npm_key,
        derive_pypi_key,
        derive_chrome_key,
        derive_ios_key,
        derive_hf_key,
        derive_ph_key,
    ],
)
def test_every_deriver_rejects_empty_input(deriver) -> None:
    with pytest.raises(ValueError):
        deriver("   ")


# ---------------------------------------------------------------------------
# Scheme precedence
# ---------------------------------------------------------------------------


def test_scheme_precedence_is_total_order() -> None:
    assert len(SCHEME_PRECEDENCE) == len(EntityScheme)
    assert set(SCHEME_PRECEDENCE) == set(EntityScheme)


def test_scheme_precedence_ranking_matches_masterplan_order() -> None:
    assert precedence_rank(EntityScheme.WEB) < precedence_rank(EntityScheme.GH)
    assert precedence_rank(EntityScheme.GH) < precedence_rank(EntityScheme.NPM)
    assert precedence_rank(EntityScheme.NPM) < precedence_rank(EntityScheme.CHROME)
    assert precedence_rank(EntityScheme.CHROME) < precedence_rank(EntityScheme.HF)
    assert precedence_rank(EntityScheme.HF) < precedence_rank(EntityScheme.PH)


# ---------------------------------------------------------------------------
# Round-trip property: derive -> str -> parse recovers the same key
# (phase doc's own testing table entry, on top of test_contracts.py's
# generic EntityKey round-trip which doesn't exercise the derivers).
# ---------------------------------------------------------------------------

_domain_label = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=10
).filter(lambda s: s.strip() != "")


@given(sub=_domain_label, domain=_domain_label)
def test_web_key_round_trips(sub: str, domain: str) -> None:
    key = derive_web_key(f"https://{sub}.{domain}.com")
    assert EntityKey.parse(str(key)) == key


@given(owner=_domain_label, repo=_domain_label)
def test_gh_key_round_trips(owner: str, repo: str) -> None:
    key = derive_gh_key(f"{owner}/{repo}")
    assert EntityKey.parse(str(key)) == key


def test_derive_key_dispatches_by_scheme() -> None:
    assert derive_key(EntityScheme.WEB, "https://acme.com") == EntityKey(
        EntityScheme.WEB, "acme.com"
    )
    assert derive_key(EntityScheme.GH, "acme/widget") == EntityKey(EntityScheme.GH, "acme/widget")
