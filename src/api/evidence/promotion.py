"""Promotion thresholds (masterplan §4.6) — when an anecdote is real enough
to become a finding. Two rules for two different evidence shapes,
deliberately not unified:

> Community themes (HN, Stack Exchange): at least 5 supporting comments
> across at least 3 distinct threads. GitHub issues: reaction-weighted
> instead, so one issue with 47 thumbs-up clears the bar where one comment
> never does.

Comment volume is a weak signal that needs breadth (distinct threads) to
mean anything; a GitHub reaction count is an explicit vote and carries more
weight per unit, so it needs none. The report prints the real N — no
invented "3,248 comments" — which is why both evaluators return the actual
count they promoted on, not just a bool.

Finding *statements* (the prose) are Phase 11's job — clustering
`complaint.<theme>`/`request.<theme>` claims into one theme first, then
calling these evaluators on the cluster. This module only decides whether
an already-clustered theme clears the bar.
"""

from __future__ import annotations

from dataclasses import dataclass

COMMENT_SUPPORT_THRESHOLD = 5
COMMENT_MIN_DISTINCT_THREADS = 3

# Masterplan §4.6 gives only a worked example ("one issue with 47 thumbs-up
# clears the bar"), not a number — this is a first-pass guess, named exactly
# like `api.resolve.maturity`'s own threshold constants, tunable in Phase 14
# against real benchmark data.
GITHUB_REACTION_THRESHOLD = 20


@dataclass(frozen=True)
class PromotionResult:
    eligible: bool
    support_count: int
    # None for reaction-weighted (GitHub) themes — breadth doesn't apply there.
    distinct_threads: int | None


def evaluate_community_theme(*, claim_ids: list[int], thread_ids: list[str]) -> PromotionResult:
    """Community themes (HN, Stack Exchange): both the volume and the breadth
    condition must hold. `claim_ids` are the already-clustered supporting
    comments for one theme; `thread_ids` may repeat (multiple comments from
    the same thread) — `distinct_threads` is what actually gates promotion."""
    support_count = len(claim_ids)
    distinct_threads = len(set(thread_ids))
    eligible = (
        support_count >= COMMENT_SUPPORT_THRESHOLD
        and distinct_threads >= COMMENT_MIN_DISTINCT_THREADS
    )
    return PromotionResult(
        eligible=eligible, support_count=support_count, distinct_threads=distinct_threads
    )


def evaluate_github_theme(*, issue_reactions: list[int]) -> PromotionResult:
    """GitHub issues: reaction-weighted, no breadth requirement. `support_count`
    here is the total reaction count across the clustered issues — the real
    N a report should print, not an issue count."""
    total_reactions = sum(issue_reactions)
    eligible = total_reactions >= GITHUB_REACTION_THRESHOLD
    return PromotionResult(eligible=eligible, support_count=total_reactions, distinct_threads=None)


__all__ = [
    "COMMENT_MIN_DISTINCT_THREADS",
    "COMMENT_SUPPORT_THRESHOLD",
    "GITHUB_REACTION_THRESHOLD",
    "PromotionResult",
    "evaluate_community_theme",
    "evaluate_github_theme",
]
