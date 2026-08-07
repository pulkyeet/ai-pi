"""Sentence-level citation binding (masterplan Rule 1; phase doc's Design
section — "the final gate, and the most mechanical"):

> for each generated section:
>     split into sentences
>     for each sentence:
>         resolve its cited finding_ids -> claim_ids
>         if no claim_ids: DROP the sentence
>     if section is now empty: omit the section

`api.synth.generate`'s three prompts require every sentence to end with an
inline citation marker naming the finding id(s) it draws from, e.g.:

    Users repeatedly report manual receipt entry as their top complaint
    [3, 7]. A lightweight OCR importer would directly address this [3].

This module is what makes that mechanical rather than inferred: split into
sentences with a real segmenter (`pysbd`, not a regex on periods — `$5.00/mo`
and `e.g.` both break naive splitting), parse each sentence's trailing
marker, resolve `finding_id -> claim_ids` against the real finding set
(never trust the marker's ids blindly — a marker naming an unknown finding
id is treated exactly like no marker at all), and drop any sentence that
doesn't resolve to at least one real claim id. Markers are stripped from
surviving prose; a report reader never sees `[3, 7]`, only the resulting
`claim_ids`. A section emptied entirely by drops is omitted, not returned as
an empty string — every function here returns `BoundSection | None`, never
a hollow one.

This is `api.synth.generate`'s aggregate `addresses_finding_ids` check's
authoritative successor, not a duplicate of it: that check is a cheap,
model-self-reported pre-filter before spending a second LLM call; this one
is verified programmatically against the real finding set, sentence by
sentence, and is what the phase doc means by "verified programmatically at
assembly... not a test."
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pysbd

from api.synth.findings import Finding

_MARKER_RE = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]")
_WHITESPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,!?;:])")
_MULTI_SPACE_RE = re.compile(r"\s+")

_segmenter = pysbd.Segmenter(language="en", clean=False)


@dataclass(frozen=True)
class BoundSection:
    statement: str
    claim_ids: tuple[int, ...]
    addresses_finding_ids: tuple[int, ...]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _segmenter.segment(text) if s.strip()]


def _sentence_finding_ids(sentence: str) -> list[int]:
    ids: list[int] = []
    for group in _MARKER_RE.findall(sentence):
        ids.extend(int(x) for x in re.split(r"\s*,\s*", group))
    return ids


def _strip_markers(sentence: str) -> str:
    stripped = _MARKER_RE.sub("", sentence)
    stripped = _WHITESPACE_BEFORE_PUNCT_RE.sub(r"\1", stripped)
    return _MULTI_SPACE_RE.sub(" ", stripped).strip()


def bind_statement(text: str, *, findings_by_id: dict[int, Finding]) -> BoundSection | None:
    """Splits `text` into sentences and keeps only those whose marker
    resolves to at least one known finding id. Returns `None` if every
    sentence was dropped (an empty section, per the phase doc, is omitted
    rather than emitted as an empty string)."""
    kept_sentences: list[str] = []
    claim_ids: list[int] = []
    finding_ids: list[int] = []

    for sentence in split_sentences(text):
        cited = [fid for fid in _sentence_finding_ids(sentence) if fid in findings_by_id]
        if not cited:
            continue
        cleaned = _strip_markers(sentence)
        if not cleaned:
            continue
        kept_sentences.append(cleaned)
        for fid in cited:
            finding_ids.append(fid)
            claim_ids.extend(findings_by_id[fid].claim_ids)

    if not kept_sentences:
        return None

    return BoundSection(
        statement=" ".join(kept_sentences),
        claim_ids=tuple(dict.fromkeys(claim_ids)),
        addresses_finding_ids=tuple(dict.fromkeys(finding_ids)),
    )


def bind_many(texts: list[str], *, findings_by_id: dict[int, Finding]) -> list[BoundSection]:
    """Binds each of `texts` independently (e.g. one per generated feature
    gap or risk); items that bind to nothing are dropped from the returned
    list, never kept as a hollow entry. An empty return means the whole
    section is omitted — the caller's job, not this function's."""
    bound = (bind_statement(t, findings_by_id=findings_by_id) for t in texts)
    return [b for b in bound if b is not None]


__all__ = ["BoundSection", "bind_many", "bind_statement", "split_sentences"]
