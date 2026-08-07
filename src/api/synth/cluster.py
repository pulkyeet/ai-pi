"""Complaint/request theme near-duplicate clustering (masterplan §11: "the
one place pgvector is used"; phase doc's own Design section).

`complaint.<theme>`/`request.<theme>` slugs arrive from extraction with open
slugs, so `receipt-ocr-accuracy` and `ocr-misreads-receipts` are the same
complaint under different names. Without clustering, both fall below
`api.evidence.promotion`'s thresholds independently and a real pain point is
lost. This module: embed each claim's theme slug plus its quote
(`api.llm.embed`), cluster by cosine similarity above a threshold, and let
the most frequent slug in a cluster be its label — `findings.py` sums
support and applies promotion **after** clustering, never before (ordering
matters: see that module).

**Where the actual clustering math runs, and why.** The embeddings
themselves are cached through pgvector storage (`embedding_cache`,
`api.llm.embed`) — that is this codebase's literal, if modest, pgvector
usage. The union-find grouping over pairwise cosine similarity happens in
plain Python after one fetch, not as a `<=>` SQL query: a run's complaint/
request claim count is small (tens, not millions), and transitive
clustering ("A~B, B~C, therefore one cluster of A/B/C even if A and C alone
fall under the threshold") isn't expressible as a single SQL query any more
than `api.evidence.contradictions`'s own per-attribute comparison rule was
— that module's docstring names the same trade-off and resolves it the same
way: compute in Python after one fetch, not in SQL.

`DEFAULT_SIMILARITY_THRESHOLD` is a first-pass constant, named exactly like
`api.resolve.maturity`'s and `api.evidence.promotion`'s own threshold
constants — tunable in Phase 14 against real, hand-labelled clusters.
Conservative on purpose: over-merging is the more damaging error (it
conflates genuinely distinct complaints into one, inflated theme), so this
starts high rather than low.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from api.llm.embed import EmbedContext, embed_texts

DEFAULT_SIMILARITY_THRESHOLD = 0.86


@dataclass(frozen=True)
class ThemeItem:
    """One `complaint.<theme>`/`request.<theme>` claim, as clustering input.
    `source_id` stands in for "which distinct thread this came from" — see
    `findings.py`'s module docstring for why that's an approximation, not an
    exact thread identity, given how `api.tasks.community` aggregates
    multiple real threads into one synthetic per-`(venue, keyword)` source."""

    claim_id: int
    slug: str
    quote: str
    source_id: int
    grade: str
    confidence: float

    @property
    def embedding_text(self) -> str:
        return f"{self.slug}: {self.quote}"


@dataclass(frozen=True)
class ThemeCluster:
    label: str
    claim_ids: tuple[int, ...]
    source_ids: tuple[int, ...]
    grades: tuple[str, ...]
    confidences: tuple[float, ...]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self._parent[ri] = rj


def cluster_by_similarity(
    items: list[ThemeItem],
    embeddings: list[list[float]],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[ThemeCluster]:
    """Union-find over pairwise cosine similarity >= `threshold` —
    transitive: if A merges with B and B merges with C, A/B/C end up in one
    cluster even if `cosine_similarity(A, C) < threshold`. `items[i]` must
    correspond to `embeddings[i]`."""
    if len(items) != len(embeddings):
        raise ValueError("items and embeddings must be the same length")

    n = len(items)
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if cosine_similarity(embeddings[i], embeddings[j]) >= threshold:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    clusters = []
    for indices in groups.values():
        slugs = [items[i].slug for i in indices]
        label = Counter(slugs).most_common(1)[0][0]
        clusters.append(
            ThemeCluster(
                label=label,
                claim_ids=tuple(items[i].claim_id for i in indices),
                source_ids=tuple(items[i].source_id for i in indices),
                grades=tuple(items[i].grade for i in indices),
                confidences=tuple(items[i].confidence for i in indices),
            )
        )
    return clusters


async def embed_and_cluster(
    items: list[ThemeItem],
    *,
    embed_ctx: EmbedContext,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[ThemeCluster]:
    if not items:
        return []
    vectors = await embed_texts([i.embedding_text for i in items], ctx=embed_ctx)
    return cluster_by_similarity(items, vectors, threshold=threshold)


__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "ThemeCluster",
    "ThemeItem",
    "cluster_by_similarity",
    "cosine_similarity",
    "embed_and_cluster",
]
