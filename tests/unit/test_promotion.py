"""Promotion thresholds (Phase 08, masterplan §4.6): both conditions
required for community themes; a different, reaction-weighted rule for
GitHub, correctly requiring no breadth.
"""

from __future__ import annotations

from api.evidence.promotion import (
    COMMENT_MIN_DISTINCT_THREADS,
    COMMENT_SUPPORT_THRESHOLD,
    GITHUB_REACTION_THRESHOLD,
    evaluate_community_theme,
    evaluate_github_theme,
)


def test_four_comments_three_threads_fails() -> None:
    result = evaluate_community_theme(claim_ids=[1, 2, 3, 4], thread_ids=["a", "a", "b", "c"])
    assert result.eligible is False
    assert result.support_count == 4
    assert result.distinct_threads == 3


def test_five_comments_three_threads_passes() -> None:
    result = evaluate_community_theme(
        claim_ids=[1, 2, 3, 4, 5], thread_ids=["a", "a", "b", "b", "c"]
    )
    assert result.eligible is True
    assert result.support_count == 5
    assert result.distinct_threads == 3


def test_five_comments_two_threads_fails() -> None:
    result = evaluate_community_theme(
        claim_ids=[1, 2, 3, 4, 5], thread_ids=["a", "a", "a", "b", "b"]
    )
    assert result.eligible is False
    assert result.support_count == 5
    assert result.distinct_threads == 2


def test_thresholds_match_module_constants() -> None:
    assert COMMENT_SUPPORT_THRESHOLD == 5
    assert COMMENT_MIN_DISTINCT_THREADS == 3


def test_a_single_reddit_comment_never_clears_the_github_bar_by_coincidence() -> None:
    # Not a real cross-rule comparison (different N meaning), just confirms
    # one comment's worth of "reactions" can never itself clear the bar.
    result = evaluate_github_theme(issue_reactions=[1])
    assert result.eligible is False


def test_one_high_reaction_issue_clears_the_github_bar() -> None:
    result = evaluate_github_theme(issue_reactions=[47])
    assert result.eligible is True
    assert result.support_count == 47
    assert result.distinct_threads is None


def test_several_low_reaction_issues_can_also_clear_by_summing() -> None:
    result = evaluate_github_theme(issue_reactions=[GITHUB_REACTION_THRESHOLD // 2] * 2)
    assert result.eligible is True
    assert result.support_count == GITHUB_REACTION_THRESHOLD


def test_below_threshold_reactions_do_not_clear() -> None:
    result = evaluate_github_theme(issue_reactions=[GITHUB_REACTION_THRESHOLD - 1])
    assert result.eligible is False
