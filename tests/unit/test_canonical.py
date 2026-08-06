from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from api.retrieval.canonical import canonicalize_url

# (input, expected) — one row per rule in the phase doc.
CASES: list[tuple[str, str]] = [
    # Lowercase scheme and host; path case preserved.
    ("HTTP://Example.COM/Path/CaseSensitive", "http://example.com/Path/CaseSensitive"),
    ("HTTPS://EXAMPLE.COM/", "https://example.com/"),
    # Default port stripped.
    ("https://example.com:443/foo", "https://example.com/foo"),
    ("http://example.com:80/foo", "http://example.com/foo"),
    # Non-default port kept.
    ("https://example.com:8443/foo", "https://example.com:8443/foo"),
    # Tracking params stripped.
    ("https://example.com/?utm_source=x&utm_campaign=y", "https://example.com/"),
    ("https://example.com/?fbclid=abc123", "https://example.com/"),
    ("https://example.com/?gclid=abc123", "https://example.com/"),
    ("https://example.com/?ref=twitter", "https://example.com/"),
    ("https://example.com/?source=hn", "https://example.com/"),
    ("https://example.com/?mc_cid=1&mc_eid=2", "https://example.com/"),
    # Real params kept and sorted by key.
    ("https://example.com/?b=2&a=1", "https://example.com/?a=1&b=2"),
    ("https://example.com/?z=1&utm_source=x&a=2", "https://example.com/?a=2&z=1"),
    # Fragment dropped.
    ("https://example.com/page#section-2", "https://example.com/page"),
    ("https://example.com/#top", "https://example.com/"),
    # Trailing slash stripped except bare root.
    ("https://example.com/pricing/", "https://example.com/pricing"),
    ("https://example.com/", "https://example.com/"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com/a/b/", "https://example.com/a/b"),
    # Percent-encoding normalised: unreserved chars decoded, hex uppercased.
    ("https://example.com/%7Euser", "https://example.com/~user"),
    ("https://example.com/foo%2fbar", "https://example.com/foo%2Fbar"),
    ("https://example.com/a%20b", "https://example.com/a%20b"),
    # Query values percent-normalised too.
    ("https://example.com/?q=%7Ehello", "https://example.com/?q=~hello"),
    # Combined: several rules firing on the same URL.
    (
        "HTTPS://Example.COM:443/Pricing/?utm_source=x&b=2&a=1#plans",
        "https://example.com/Pricing?a=1&b=2",
    ),
]


def test_canonicalisation_table() -> None:
    for raw, expected in CASES:
        assert canonicalize_url(raw) == expected, f"canonicalize_url({raw!r})"


def test_idempotent_on_the_table_itself() -> None:
    for _raw, expected in CASES:
        assert canonicalize_url(expected) == expected


# A structured URL strategy: fuzzing raw strings mostly produces invalid URLs
# that don't exercise the rules under test. Building from parts keeps every
# generated example a URL canonicalize_url is meant to handle.
_HOST = st.from_regex(r"[a-z]{2,10}\.(com|io|dev)", fullmatch=True)
_PATH_SEGMENT = st.from_regex(r"[a-zA-Z0-9_-]{1,10}", fullmatch=True)
_PATH = st.lists(_PATH_SEGMENT, max_size=4).map(lambda segs: "/" + "/".join(segs))
_QUERY_KEY = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_]{0,8}", fullmatch=True)
_QUERY_VALUE = st.from_regex(r"[a-zA-Z0-9_-]{0,10}", fullmatch=True)
_QUERY = st.dictionaries(_QUERY_KEY, _QUERY_VALUE, max_size=4)
_FRAGMENT = st.one_of(st.none(), st.from_regex(r"[a-zA-Z0-9_-]{0,10}", fullmatch=True))


@st.composite
def _urls(draw: st.DrawFn) -> str:
    scheme = draw(st.sampled_from(["http", "https", "HTTP", "HTTPS"]))
    host = draw(_HOST)
    path = draw(_PATH)
    query = draw(_QUERY)
    fragment = draw(_FRAGMENT)

    url = f"{scheme}://{host}{path}"
    if query:
        url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    if fragment is not None:
        url += f"#{fragment}"
    return url


@given(url=_urls())
def test_canonicalize_url_is_idempotent(url: str) -> None:
    once = canonicalize_url(url)
    twice = canonicalize_url(once)
    assert once == twice
