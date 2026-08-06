from __future__ import annotations

from api.retrieval.pathguess import (
    CHANGELOG_PATHS,
    DOCS_PATHS,
    MAX_ATTEMPTS_PER_DOMAIN,
    PRICING_PATHS,
    candidate_paths,
    looks_price_shaped,
)

# True positives from the phase doc's own test spec.
_TRUE_POSITIVES = ["$5/mo", "€12 per seat", "Free", "Starting at $99/month", "₹200 per user"]
# True negatives from the phase doc's own test spec.
_TRUE_NEGATIVES = ["5 stars", "2024", "Contact us for details", "About our team"]


def test_price_token_true_positives() -> None:
    for text in _TRUE_POSITIVES:
        assert looks_price_shaped(text), text


def test_price_token_true_negatives() -> None:
    for text in _TRUE_NEGATIVES:
        assert not looks_price_shaped(text), text


def test_candidate_paths_default_matches_declared_order() -> None:
    assert candidate_paths("pricing") == PRICING_PATHS[:MAX_ATTEMPTS_PER_DOMAIN]
    assert candidate_paths("docs") == DOCS_PATHS[:MAX_ATTEMPTS_PER_DOMAIN]
    assert candidate_paths("changelog") == CHANGELOG_PATHS[:MAX_ATTEMPTS_PER_DOMAIN]


def test_candidate_paths_respects_attempt_cap() -> None:
    assert len(candidate_paths("pricing")) <= MAX_ATTEMPTS_PER_DOMAIN
    # PRICING_PATHS itself has 7 entries — more than the cap — so the cap
    # must actually be trimming, not just happening to match the list length.
    assert len(PRICING_PATHS) > MAX_ATTEMPTS_PER_DOMAIN
    assert candidate_paths("pricing", limit=2) == PRICING_PATHS[:2]


def test_pricing_paths_ordered_pricing_first() -> None:
    assert PRICING_PATHS[0] == "/pricing"
