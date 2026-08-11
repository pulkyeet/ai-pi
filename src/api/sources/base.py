"""`Retriever` marker protocol (masterplan §5/§7, Phase 04).

Deliberately thin: the phase doc's own retriever table shows wildly
different shapes per source (repo metadata vs. download counts vs. search
snippets), so one uniform `fetch(**kwargs)` method would erase type safety
for no real benefit. Each retriever module below exposes its own typed
async methods and Pydantic record models; this protocol only names what
every retriever has in common — an identity and a dominant evidence grade
(masterplan §4.6) — so callers can enumerate/log/report on retrievers
generically without knowing their concrete type.
"""

from __future__ import annotations

from typing import Protocol


class Retriever(Protocol):
    name: str
    grade: str  # dominant masterplan §4.6 evidence grade for this source, e.g. "A", "D"


class RetrieverUnavailableError(Exception):
    """A retriever's data isn't reachable right now — missing/insufficient
    credentials, a feature flag off, or a vendor-reported quota/permission
    exhausted. Never a run failure: the caller (a future Phase 10 task
    handler) converts this into a coverage gap, the same mechanism the
    credential-gated retrievers already model end to end."""

    def __init__(self, retriever: str, reason: str) -> None:
        super().__init__(f"{retriever} unavailable: {reason}")
        self.retriever = retriever
        self.reason = reason


__all__ = ["Retriever", "RetrieverUnavailableError"]
