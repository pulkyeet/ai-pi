"""`api.synth.cluster`'s pure clustering math — no Postgres, no LLM. The
over-merge guard the phase doc names explicitly: two genuinely distinct
complaints must not collapse into one cluster just because they share a
domain vocabulary word."""

from __future__ import annotations

import math

import pytest

from api.synth.cluster import ThemeItem, cluster_by_similarity, cosine_similarity


def _item(claim_id: int, slug: str, *, source_id: int = 1, grade: str = "D") -> ThemeItem:
    return ThemeItem(
        claim_id=claim_id, slug=slug, quote="q", source_id=source_id, grade=grade, confidence=0.5
    )


def test_cosine_similarity_of_identical_vectors_is_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_of_opposite_vectors_is_negative_one() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_is_zero_not_nan() -> None:
    result = cosine_similarity([0.0, 0.0], [1.0, 1.0])
    assert result == 0.0
    assert not math.isnan(result)


def test_cosine_similarity_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        cosine_similarity([1.0, 2.0], [1.0])


def test_near_duplicate_themes_merge_into_one_cluster() -> None:
    """The masterplan's own example: 'receipt-ocr-accuracy' and
    'ocr-misreads-receipts' are the same complaint under different slugs."""
    items = [_item(1, "receipt-ocr-accuracy"), _item(2, "ocr-misreads-receipts")]
    embeddings = [[1.0, 0.0], [0.99, 0.01]]

    clusters = cluster_by_similarity(items, embeddings, threshold=0.9)

    assert len(clusters) == 1
    assert set(clusters[0].claim_ids) == {1, 2}


def test_distinct_complaints_do_not_merge() -> None:
    """The over-merge guard: two unrelated complaints, at low similarity,
    must survive as two separate clusters, not one inflated theme."""
    items = [_item(1, "slow-support"), _item(2, "confusing-onboarding")]
    embeddings = [[1.0, 0.0], [0.0, 1.0]]

    clusters = cluster_by_similarity(items, embeddings, threshold=0.86)

    assert len(clusters) == 2
    claim_id_sets = {frozenset(c.claim_ids) for c in clusters}
    assert claim_id_sets == {frozenset({1}), frozenset({2})}


def test_clustering_is_transitive() -> None:
    """A~B and B~C above threshold merges all three, even though
    cosine_similarity(A, C) alone falls under it — union-find, not
    pairwise-only grouping. Three unit vectors 25 degrees apart: adjacent
    pairs sit just above a 0.9 threshold, the endpoints (50 degrees apart)
    sit well below it."""
    items = [_item(1, "a"), _item(2, "b"), _item(3, "c")]
    embeddings = [
        [math.cos(math.radians(0)), math.sin(math.radians(0))],
        [math.cos(math.radians(25)), math.sin(math.radians(25))],
        [math.cos(math.radians(50)), math.sin(math.radians(50))],
    ]
    assert cosine_similarity(embeddings[0], embeddings[1]) > 0.9
    assert cosine_similarity(embeddings[1], embeddings[2]) > 0.9
    assert cosine_similarity(embeddings[0], embeddings[2]) < 0.9

    clusters = cluster_by_similarity(items, embeddings, threshold=0.9)

    assert len(clusters) == 1
    assert set(clusters[0].claim_ids) == {1, 2, 3}


def test_cluster_label_is_the_most_frequent_slug() -> None:
    items = [_item(1, "manual-entry"), _item(2, "manual-entry"), _item(3, "manual-data-entry")]
    embeddings = [[1.0, 0.0], [1.0, 0.0], [0.99, 0.01]]

    clusters = cluster_by_similarity(items, embeddings, threshold=0.9)

    assert len(clusters) == 1
    assert clusters[0].label == "manual-entry"


def test_cluster_aggregates_source_ids_grades_and_confidences() -> None:
    items = [
        ThemeItem(claim_id=1, slug="x", quote="q", source_id=10, grade="D", confidence=0.3),
        ThemeItem(claim_id=2, slug="x", quote="q", source_id=11, grade="C", confidence=0.7),
    ]
    embeddings = [[1.0, 0.0], [1.0, 0.0]]

    clusters = cluster_by_similarity(items, embeddings, threshold=0.9)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert set(cluster.source_ids) == {10, 11}
    assert set(cluster.grades) == {"D", "C"}
    assert set(cluster.confidences) == {0.3, 0.7}


def test_cluster_by_similarity_rejects_mismatched_item_and_embedding_counts() -> None:
    with pytest.raises(ValueError, match="same length"):
        cluster_by_similarity([_item(1, "a")], [[1.0], [2.0]])


def test_empty_input_produces_no_clusters() -> None:
    assert cluster_by_similarity([], []) == []
