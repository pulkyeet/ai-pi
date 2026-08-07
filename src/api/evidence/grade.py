"""Evidence grading (masterplan §4.6) — `Grade` assigned mechanically from
retrieval provenance, never from model judgement. This is one of the two
things that make this phase "entirely deterministic" (phase doc's own
framing) — the other is `api.evidence.confidence`.

`SourceKind` is this module's own closed vocabulary over masterplan §4.6's
grading table and §5's source registry. It is not a stored contract — only
the resulting `Grade` is ever persisted onto a `Claim`. Most of it is
already fixed at the retriever level (`api.sources.*`'s own `grade` class
attributes are each retriever's *dominant* grade — see `api.sources.base`'s
docstring), so a caller building a claim from an HN comment, a Stack
Exchange answer, an npm download count, or a SERP-read aggregator snippet
already knows its grade directly from that attribute and never needs this
module. The two provenance decisions those flat attributes can't make on
their own are what `grade_for`/`classify_own_domain_fetch` exist for:

- A page fetched from an entity's own domain (`api.retrieval.fetch_source`)
  is graded per its *path* — a pricing/docs page structurally different
  from a blog/changelog post — which `Source` itself doesn't distinguish.
- GitHub is genuinely bifurcated ("A / D" per masterplan §5): the same
  retriever returns `GitHubRepo` (structured API metadata, A) and
  `GitHubIssue` (a community leaderboard entry, D) from different calls,
  so no single flat attribute can cover both.
- A Wayback snapshot inherits the grade its original artifact would have
  had, capped at B — a historical capture is never better evidence than a
  live primary source, however solid the original grade was.

Deciding *which* `SourceKind` applies to a given fetch (own-domain vs.
aggregator vs. community, which record type a GitHub response is) is a
caller concern, same as every other opportunistically-observed fact in this
codebase (see `api.resolve.types`'s module docstring) — this module only
fixes the mapping once that classification has already been made.
"""

from __future__ import annotations

from enum import StrEnum

from api.models.claims import Grade
from api.retrieval.pathguess import CHANGELOG_PATHS


class SourceKind(StrEnum):
    """One entry per row of masterplan §4.6's grading table plus §5's
    source registry that isn't already covered by a retriever's own flat
    `grade` attribute (`api.sources.base.Retriever`)."""

    OWN_DOMAIN_STRUCTURED = "own_domain_structured"  # pricing/docs page, entity's own domain
    OWN_DOMAIN_PROSE = "own_domain_prose"  # blog/changelog page, entity's own domain
    STRUCTURED_API = "structured_api"  # GitHub repo metadata, package registry counts
    LAUNCH_ANNOUNCEMENT = "launch_announcement"  # Product Hunt
    AGGREGATOR = "aggregator"  # G2/Capterra/Crunchbase/StackShare, read via SERP snippet
    COMMUNITY = "community"  # Reddit/HN comment, GitHub issue, Stack Exchange answer
    WAYBACK_SNAPSHOT = "wayback_snapshot"  # historical capture of another kind


_BASE_GRADE: dict[SourceKind, Grade] = {
    SourceKind.OWN_DOMAIN_STRUCTURED: Grade.A,
    SourceKind.STRUCTURED_API: Grade.A,
    SourceKind.OWN_DOMAIN_PROSE: Grade.B,
    SourceKind.LAUNCH_ANNOUNCEMENT: Grade.B,
    SourceKind.AGGREGATOR: Grade.C,
    SourceKind.COMMUNITY: Grade.D,
}

_RANK: dict[Grade, int] = {Grade.A: 0, Grade.B: 1, Grade.C: 2, Grade.D: 3}
_BY_RANK: dict[int, Grade] = {rank: grade for grade, rank in _RANK.items()}

# A Wayback capture can never grade better than this, however good the
# original was — it is definitionally historical, not live, evidence.
WAYBACK_CAP: Grade = Grade.B

# Reuses Phase 03's own path-kind constant as the single source of truth for
# "changelog-shaped path" rather than redeclaring it (the same convention
# `api.sources.serp_snippets` follows for `NO_CRAWL_DOMAINS`). Plain "/blog"
# is added because `CHANGELOG_PATHS` only lists the more specific
# "/blog/changelog" — masterplan §4.6 names "/blog" itself as prose too.
_PROSE_PATH_PREFIXES: tuple[str, ...] = (*CHANGELOG_PATHS, "/blog")


def grade_for(kind: SourceKind, *, wayback_of: SourceKind | None = None) -> Grade:
    """`wayback_of` is required exactly when `kind` is `WAYBACK_SNAPSHOT` —
    the `SourceKind` the original artifact would have been graded as, before
    the cap. A Wayback capture of a live pricing page
    (`OWN_DOMAIN_STRUCTURED`, grade A) is a *historical* pricing page, not a
    live one, so it is capped down to B; a capture of an already-worse
    source (e.g. a forum thread, grade D) stays at its original, worse
    grade — the cap only ever pulls a grade down, never up.
    """
    if kind is SourceKind.WAYBACK_SNAPSHOT:
        if wayback_of is None or wayback_of is SourceKind.WAYBACK_SNAPSHOT:
            raise ValueError("wayback_of must name the underlying, non-Wayback source kind")
        underlying_rank = _RANK[_BASE_GRADE[wayback_of]]
        return _BY_RANK[max(underlying_rank, _RANK[WAYBACK_CAP])]
    if wayback_of is not None:
        raise ValueError("wayback_of is only meaningful when kind is WAYBACK_SNAPSHOT")
    return _BASE_GRADE[kind]


def classify_own_domain_fetch(path: str) -> SourceKind:
    """For a page already known to have been fetched from the entity's own
    registrable domain: a prose path (`/blog`, `/changelog`, ...) is B;
    anything else — the homepage, `/pricing`, `/docs` — is A, per
    masterplan §4.6's "structured primary" assumption for a product's own
    site. Not responsible for deciding *whether* a fetch was on the
    entity's own domain; that comparison already happened by the time a
    caller has a `Source` to grade at all.
    """
    normalised = path if path.startswith("/") else f"/{path}"
    trimmed = normalised.rstrip("/") or "/"
    if any(trimmed.startswith(prefix) for prefix in _PROSE_PATH_PREFIXES):
        return SourceKind.OWN_DOMAIN_PROSE
    return SourceKind.OWN_DOMAIN_STRUCTURED


__all__ = [
    "WAYBACK_CAP",
    "SourceKind",
    "classify_own_domain_fetch",
    "grade_for",
]
