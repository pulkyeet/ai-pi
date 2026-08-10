from __future__ import annotations

import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, field_validator, model_validator

_FAMILY_FEATURE = re.compile(r"^feature\.[a-z0-9-]{2,40}\.present$")
_FAMILY_COMPLAINT = re.compile(r"^complaint\.[a-z0-9-]{2,40}$")
_FAMILY_REQUEST_REACTIONS = re.compile(r"^request\.[a-z0-9-]{2,40}\.reactions$")
_FAMILY_REQUEST = re.compile(r"^request\.[a-z0-9-]{2,40}$")


class ClaimAttribute(StrEnum):
    """Closed enumerable attributes (masterplan §4.4).

    Parameterised families (feature.*, complaint.*, request.*) are validated
    separately by regex — see `validate_claim_attribute` — because their slug
    is open but their shape is closed.
    """

    PRICING_MODEL = "pricing.model"
    PRICING_ENTRY_USD_MONTH = "pricing.entry_usd_month"
    PRICING_FREE_TIER = "pricing.free_tier"
    PRICING_TRIAL_DAYS = "pricing.trial_days"
    PRODUCT_LAUNCH_DATE = "product.launch_date"
    PRODUCT_PLATFORMS = "product.platforms"
    PRODUCT_INTEGRATIONS = "product.integrations"
    COMPANY_FUNDING_TOTAL = "company.funding_total_usd"
    COMPANY_STAGE = "company.stage"
    OSS_REPO = "oss.repo"
    OSS_STARS = "oss.stars"
    OSS_STARS_90D_DELTA = "oss.stars_90d_delta"
    OSS_LAST_COMMIT_AT = "oss.last_commit_at"
    OSS_LICENSE = "oss.license"
    OSS_CONTRIBUTORS_90D = "oss.contributors_90d"


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ValueKind(StrEnum):
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    ENUM = "enum"
    TEXT = "text"
    DATE = "date"
    LIST = "list"


class AttributeSpec(BaseModel):
    kind: ValueKind
    unit: str | None = None
    choices: frozenset[str] | None = None


ATTRIBUTE_SPEC: dict[str, AttributeSpec] = {
    ClaimAttribute.PRICING_MODEL: AttributeSpec(
        # "free" (Phase 14 finding): a genuinely, permanently free product
        # (no paid tier at all) has no honest member of the original four —
        # "freemium" implies a paid tier exists above the free one, which
        # would be a fabricated claim for e.g. an OSS tool with no pricing
        # page. Without this, such a product can never complete the pricing
        # triple `build_competitors` requires and is structurally invisible
        # to `report.competitors` no matter how well it was discovered.
        kind=ValueKind.ENUM,
        choices=frozenset({"seat", "usage", "flat", "freemium", "free"}),
    ),
    ClaimAttribute.PRICING_ENTRY_USD_MONTH: AttributeSpec(kind=ValueKind.NUMERIC, unit="usd/month"),
    ClaimAttribute.PRICING_FREE_TIER: AttributeSpec(kind=ValueKind.BOOLEAN),
    ClaimAttribute.PRICING_TRIAL_DAYS: AttributeSpec(kind=ValueKind.NUMERIC, unit="days"),
    ClaimAttribute.PRODUCT_LAUNCH_DATE: AttributeSpec(kind=ValueKind.DATE),
    ClaimAttribute.PRODUCT_PLATFORMS: AttributeSpec(kind=ValueKind.LIST),
    ClaimAttribute.PRODUCT_INTEGRATIONS: AttributeSpec(kind=ValueKind.LIST),
    ClaimAttribute.COMPANY_FUNDING_TOTAL: AttributeSpec(kind=ValueKind.NUMERIC, unit="usd"),
    ClaimAttribute.COMPANY_STAGE: AttributeSpec(kind=ValueKind.TEXT),
    ClaimAttribute.OSS_REPO: AttributeSpec(kind=ValueKind.TEXT),
    ClaimAttribute.OSS_STARS: AttributeSpec(kind=ValueKind.NUMERIC),
    ClaimAttribute.OSS_STARS_90D_DELTA: AttributeSpec(kind=ValueKind.NUMERIC),
    ClaimAttribute.OSS_LAST_COMMIT_AT: AttributeSpec(kind=ValueKind.DATE),
    ClaimAttribute.OSS_LICENSE: AttributeSpec(kind=ValueKind.TEXT),
    ClaimAttribute.OSS_CONTRIBUTORS_90D: AttributeSpec(kind=ValueKind.NUMERIC),
}

_FEATURE_SPEC = AttributeSpec(kind=ValueKind.BOOLEAN)
_COMPLAINT_SPEC = AttributeSpec(kind=ValueKind.TEXT)
_REQUEST_SPEC = AttributeSpec(kind=ValueKind.TEXT)
_REQUEST_REACTIONS_SPEC = AttributeSpec(kind=ValueKind.NUMERIC)


def validate_claim_attribute(value: str) -> str:
    """Raise ValueError unless `value` is a declared attribute or a well-shaped
    parameterised family member. This is the single gate that keeps the claim
    vocabulary closed."""
    if value in ATTRIBUTE_SPEC:
        return value
    for pattern in (_FAMILY_FEATURE, _FAMILY_COMPLAINT, _FAMILY_REQUEST_REACTIONS, _FAMILY_REQUEST):
        if pattern.match(value):
            return value
    raise ValueError(f"unknown or malformed claim attribute: {value!r}")


def attribute_spec(attribute: str) -> AttributeSpec:
    if attribute in ATTRIBUTE_SPEC:
        return ATTRIBUTE_SPEC[attribute]
    if _FAMILY_FEATURE.match(attribute):
        return _FEATURE_SPEC
    if _FAMILY_COMPLAINT.match(attribute):
        return _COMPLAINT_SPEC
    if _FAMILY_REQUEST_REACTIONS.match(attribute):
        return _REQUEST_REACTIONS_SPEC
    if _FAMILY_REQUEST.match(attribute):
        return _REQUEST_SPEC
    raise ValueError(f"unknown or malformed claim attribute: {attribute!r}")


class Claim(BaseModel):
    id: int | None = None
    run_id: str
    entity_id: int
    attribute: str
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    as_of: date | None = None
    source_id: int
    quote: str
    char_start: int
    char_end: int
    quote_context: str
    context_offset: int
    grade: Grade
    extractor_version: str
    confidence: float
    superseded_by: int | None = None

    @field_validator("attribute")
    @classmethod
    def _attribute_is_valid(cls, v: str) -> str:
        return validate_claim_attribute(v)

    @model_validator(mode="after")
    def _value_matches_spec(self) -> Claim:
        spec = attribute_spec(self.attribute)
        if spec.kind is ValueKind.NUMERIC and self.value_num is None:
            raise ValueError(f"{self.attribute} is numeric and requires value_num")
        if spec.kind is ValueKind.BOOLEAN and self.value_text not in ("true", "false"):
            raise ValueError(
                f"{self.attribute} is boolean and requires value_text in {{'true', 'false'}}"
            )
        if spec.kind is ValueKind.ENUM:
            choices = spec.choices or frozenset()
            if self.value_text not in choices:
                raise ValueError(f"{self.attribute} requires value_text in {sorted(choices)}")
        if self.char_end <= self.char_start or self.char_start < 0:
            raise ValueError(
                "char_end must be greater than char_start, and char_start must be >= 0"
            )
        return self
