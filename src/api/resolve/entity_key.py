"""Entity key derivation per scheme (masterplan §4.5, phase doc's own Design
section). Every function here is a pure string -> `EntityKey` transform; no
I/O, no verification (that's `api.resolve.verify`) and no persistence
(that's `api.resolve.store`).

The PSL flag is the whole trick for `web:` keys. `tldextract`'s default
(`include_psl_private_domains=False`) treats every entry on the Public
Suffix List's *private* section — `fly.dev`, `vercel.app`, `github.io`, and
friends — as an ordinary suffix, which collapses every product hosted on
one of those platforms into a single entity keyed by the bare platform
domain. `include_psl_private_domains=True` makes `tldextract` treat those
entries the way they are actually used: as multi-tenant hosts, one
registrable domain per tenant. This is not the default, it is easy to lose
in a refactor, and it is the single flag standing between correct behaviour
and a report claiming "43 competitors" when 40 of them are weekend projects
sharing `fly.dev` — see `tests/unit/test_entity_key.py`'s PSL table test.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import tldextract

from api.models.entity import EntityKey, EntityScheme

# Two extractors, deliberately: comparing their output is how `is_paas_host`
# detects "this registrable domain only exists because it's on the PSL's
# *private* section" without tldextract exposing that as a direct flag.
_extract_all = tldextract.TLDExtract(include_psl_private_domains=True)
_extract_public = tldextract.TLDExtract(include_psl_private_domains=False)

# Masterplan §4.5: web > gh > npm/pypi > chrome/ios/hf > ph. Lower index wins
# when two keys are merged (`api.resolve.alias.canonical_pair`).
SCHEME_PRECEDENCE: tuple[EntityScheme, ...] = (
    EntityScheme.WEB,
    EntityScheme.GH,
    EntityScheme.NPM,
    EntityScheme.PYPI,
    EntityScheme.CHROME,
    EntityScheme.IOS,
    EntityScheme.HF,
    EntityScheme.PH,
)

# `repository`-field values from npm/PyPI metadata arrive in several real
# shapes beyond a plain https URL: `git+https://...`, `git://...`,
# `ssh://git@github.com/...`, and the scp-like `git@github.com:owner/repo`.
_GH_PREFIX_RE = re.compile(r"^(?:git\+)?(?:https?|ssh|git)://(?:[^@/]+@)?", re.IGNORECASE)
_GH_SCP_RE = re.compile(
    r"^(?:git\+)?git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.IGNORECASE
)
_GH_URL_RE = re.compile(r"^(?:www\.)?github\.com/([^/\s]+)/([^/\s]+?)/?$", re.IGNORECASE)
_PEP503_RUN_RE = re.compile(r"[-_.]+")


def precedence_rank(scheme: EntityScheme) -> int:
    return SCHEME_PRECEDENCE.index(scheme)


def is_paas_host(host: str) -> bool:
    """True when `host`'s registrable domain only differs from its
    public-suffix-only registrable domain because of a PSL *private* entry
    — i.e. `host` is a tenant subdomain of a shared PaaS, not its own
    registered domain."""
    return (
        _extract_public(host).top_domain_under_public_suffix
        != _extract_all(host).top_domain_under_public_suffix
    )


def derive_web_key(url_or_host: str) -> EntityKey:
    """Registrable domain, PSL-private-aware. Strips `www.`, any other
    subdomain, scheme, path, and query — all of that already falls out of
    using `registered_domain` rather than the full host."""
    result = _extract_all(url_or_host.strip())
    registered = result.top_domain_under_public_suffix
    if not registered:
        raise ValueError(f"cannot derive web: key from {url_or_host!r}")
    return EntityKey(EntityScheme.WEB, registered.lower())


def derive_gh_key(raw: str) -> EntityKey:
    """Accepts a bare `owner/repo`, a full `github.com/...` URL (any of
    `https://`, `git://`, `ssh://`, `git+https://`), the scp-like
    `git@github.com:owner/repo` form, or any of those with a trailing
    `.git` / trailing slash."""
    raw = raw.strip()
    scp_match = _GH_SCP_RE.match(raw)
    if scp_match:
        owner, repo = scp_match.group(1), scp_match.group(2)
    else:
        normalized = _GH_PREFIX_RE.sub("", raw)
        match = _GH_URL_RE.match(normalized)
        if match:
            owner, repo = match.group(1), match.group(2)
        else:
            parts = raw.strip("/").split("/")
            if len(parts) != 2 or not all(parts):
                raise ValueError(f"cannot derive gh: key from {raw!r}")
            owner, repo = parts
    repo = re.sub(r"\.git$", "", repo, flags=re.IGNORECASE)
    if not owner or not repo:
        raise ValueError(f"cannot derive gh: key from {raw!r}")
    return EntityKey(EntityScheme.GH, f"{owner.lower()}/{repo.lower()}")


def derive_npm_key(raw: str) -> EntityKey:
    """npm enforces lowercase package names (scoped or not) at publish
    time; lowercasing here just matches registry reality."""
    value = raw.strip().lower()
    if not value:
        raise ValueError(f"cannot derive npm: key from {raw!r}")
    return EntityKey(EntityScheme.NPM, value)


def derive_pypi_key(raw: str) -> EntityKey:
    """PEP 503 normalisation: lowercase, runs of `-_.` collapse to one `-`."""
    value = raw.strip()
    if not value:
        raise ValueError(f"cannot derive pypi: key from {raw!r}")
    normalized = _PEP503_RUN_RE.sub("-", value).lower()
    return EntityKey(EntityScheme.PYPI, normalized)


def derive_chrome_key(ext_id: str) -> EntityKey:
    value = ext_id.strip().lower()
    if not value:
        raise ValueError(f"cannot derive chrome: key from {ext_id!r}")
    return EntityKey(EntityScheme.CHROME, value)


def derive_ios_key(app_id: str) -> EntityKey:
    value = app_id.strip().lower()
    if not value:
        raise ValueError(f"cannot derive ios: key from {app_id!r}")
    return EntityKey(EntityScheme.IOS, value)


def derive_hf_key(raw: str) -> EntityKey:
    value = raw.strip().lower()
    if not value:
        raise ValueError(f"cannot derive hf: key from {raw!r}")
    return EntityKey(EntityScheme.HF, value)


def derive_ph_key(slug: str) -> EntityKey:
    value = slug.strip().lower()
    if not value:
        raise ValueError(f"cannot derive ph: key from {slug!r}")
    return EntityKey(EntityScheme.PH, value)


_DERIVERS: dict[EntityScheme, Callable[[str], EntityKey]] = {
    EntityScheme.WEB: derive_web_key,
    EntityScheme.GH: derive_gh_key,
    EntityScheme.NPM: derive_npm_key,
    EntityScheme.PYPI: derive_pypi_key,
    EntityScheme.CHROME: derive_chrome_key,
    EntityScheme.IOS: derive_ios_key,
    EntityScheme.HF: derive_hf_key,
    EntityScheme.PH: derive_ph_key,
}


def derive_key(scheme: EntityScheme, raw: str) -> EntityKey:
    return _DERIVERS[scheme](raw)


__all__ = [
    "SCHEME_PRECEDENCE",
    "derive_chrome_key",
    "derive_gh_key",
    "derive_hf_key",
    "derive_ios_key",
    "derive_key",
    "derive_npm_key",
    "derive_ph_key",
    "derive_pypi_key",
    "derive_web_key",
    "is_paas_host",
    "precedence_rank",
]
