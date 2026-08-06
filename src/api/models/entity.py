from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EntityScheme(StrEnum):
    WEB = "web"
    GH = "gh"
    NPM = "npm"
    PYPI = "pypi"
    CHROME = "chrome"
    IOS = "ios"
    HF = "hf"
    PH = "ph"


class Maturity(StrEnum):
    ESTABLISHED = "established"
    FUNDED = "funded"
    INDIE = "indie"
    HOBBY = "hobby"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class EntityKey:
    """Scheme-prefixed entity identity (masterplan §4.5).

    Derivation (PSL handling, alias merging) belongs to Phase 07; this type
    only fixes the shape and its parse/format round-trip.
    """

    scheme: EntityScheme
    value: str

    def __str__(self) -> str:
        return f"{self.scheme}:{self.value}"

    @classmethod
    def parse(cls, raw: str) -> EntityKey:
        scheme_str, sep, value = raw.partition(":")
        if not sep or not value:
            raise ValueError(f"invalid entity key, expected 'scheme:value': {raw!r}")
        try:
            scheme = EntityScheme(scheme_str)
        except ValueError as exc:
            raise ValueError(f"unknown entity scheme: {scheme_str!r}") from exc
        return cls(scheme=scheme, value=value)
