"""Constrained synthesis (masterplan §4.9, phase doc's Design section) — the
guard against this product category's standard failure mode, generic advice.

Three generated outputs — MVP statement, feature-gap statements, risk
statements — each receives **only the resolved finding set**, never raw
page text or claim quotes: `[id] kind=... support=... confidence=...
statement` per finding, nothing else. This is both the anti-generic-advice
guard and a second injection boundary — page text reached a model once,
under closed-vocabulary constraint, in extraction, and never again.

This module runs the **first**, cheap gate: the model self-reports which
finding ids a generated statement draws from (`addresses_finding_ids`), and
that aggregate is rejected if it cites fewer than three distinct findings,
or zero `pain_point`-kind findings — both per the masterplan, the second
being "the sharper condition" (an MVP addressing no user complaint was not
derived from anything users said). `api.synth.bind` runs the **second**,
authoritative gate one layer up: per-sentence citation markers inside the
generated prose itself, mechanically verified rather than trusted from the
model's own summary field.

**Rejection handling**: one repair attempt naming the specific violation
(mirrors `api.planner.plan`'s own one-repair-round shape for a
schema-valid-but-domain-invalid response), then the section is omitted —
never emitted with a loosened check. If the finding set can't possibly
satisfy the rule (fewer than three findings exist at all, or zero
`pain_point` findings exist), no LLM call is made at all — the outcome is
already determined, and the masterplan's cost story doesn't include paying
for a call that can't succeed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from api.llm.gateway import LLMContext, LLMValidationError, structured
from api.synth.findings import Finding, FindingKind

MIN_FINDINGS_REFERENCED = 3

MVP_PROMPT_ID = "synthesise_mvp"
GAPS_PROMPT_ID = "synthesise_gaps"
RISKS_PROMPT_ID = "synthesise_risks"


class MVPResponse(BaseModel):
    statement: str
    addresses_finding_ids: list[int]


class GeneratedStatement(BaseModel):
    statement: str
    addresses_finding_ids: list[int]


class FeatureGapsResponse(BaseModel):
    gaps: list[GeneratedStatement]


class RisksResponse(BaseModel):
    risks: list[GeneratedStatement]


@dataclass(frozen=True)
class SynthesisResult:
    mvp: MVPResponse | None
    feature_gaps: FeatureGapsResponse | None
    risks: RisksResponse | None
    # Section names omitted for failing the finding-count/complaint guard —
    # `api.synth.assemble` folds these into the report's coverage gaps.
    omitted_sections: tuple[str, ...]


def _fmt_confidence(confidence: float | None) -> str:
    return f"{confidence:.2f}" if confidence is not None else "n/a"


def _findings_block(findings: list[Finding]) -> str:
    return "\n".join(
        f"[{f.id}] kind={f.kind.value} support={f.support_count} "
        f"confidence={_fmt_confidence(f.confidence)} {f.statement}"
        for f in findings
    )


def _can_possibly_satisfy(findings: list[Finding]) -> bool:
    complaint_count = sum(1 for f in findings if f.kind is FindingKind.PAIN_POINT)
    return len(findings) >= MIN_FINDINGS_REFERENCED and complaint_count >= 1


def _violation(finding_ids: list[int], *, all_ids: set[int], complaint_ids: set[int]) -> str | None:
    cited = set(finding_ids)
    unknown = cited - all_ids
    if unknown:
        return f"cites unknown finding_ids: {sorted(unknown)}"
    if len(cited) < MIN_FINDINGS_REFERENCED:
        return f"cites {len(cited)} distinct findings, need at least {MIN_FINDINGS_REFERENCED}"
    if cited.isdisjoint(complaint_ids):
        return "cites zero pain_point-derived findings"
    return None


async def _attempt[T: BaseModel](
    schema: type[T],
    prompt_id: str,
    variables: dict[str, str],
    *,
    ctx: LLMContext,
    all_ids: set[int],
    complaint_ids: set[int],
    cited_ids: Callable[[T], list[int]],
) -> tuple[T | None, str | None]:
    result = await structured(schema, prompt_id, variables, ctx=ctx)
    violation = _violation(cited_ids(result.value), all_ids=all_ids, complaint_ids=complaint_ids)
    return (result.value, None) if violation is None else (None, violation)


async def _generate_with_repair[T: BaseModel](
    schema: type[T],
    prompt_id: str,
    base_variables: dict[str, str],
    *,
    ctx: LLMContext,
    all_ids: set[int],
    complaint_ids: set[int],
    cited_ids: Callable[[T], list[int]],
) -> T | None:
    try:
        value, violation = await _attempt(
            schema,
            prompt_id,
            base_variables,
            ctx=ctx,
            all_ids=all_ids,
            complaint_ids=complaint_ids,
            cited_ids=cited_ids,
        )
    except LLMValidationError:
        return None
    if violation is None:
        return value

    repair_variables = {
        **base_variables,
        "repair_note": (
            f"Your previous output was rejected for this reason: {violation}. "
            "Produce corrected output that fixes it."
        ),
    }
    try:
        value, violation = await _attempt(
            schema,
            prompt_id,
            repair_variables,
            ctx=ctx,
            all_ids=all_ids,
            complaint_ids=complaint_ids,
            cited_ids=cited_ids,
        )
    except LLMValidationError:
        return None
    return value if violation is None else None


async def generate_mvp(findings: list[Finding], *, ctx: LLMContext) -> MVPResponse | None:
    if not _can_possibly_satisfy(findings):
        return None
    all_ids = {f.id for f in findings}
    complaint_ids = {f.id for f in findings if f.kind is FindingKind.PAIN_POINT}
    base_variables = {"findings": _findings_block(findings), "repair_note": ""}
    return await _generate_with_repair(
        MVPResponse,
        MVP_PROMPT_ID,
        base_variables,
        ctx=ctx,
        all_ids=all_ids,
        complaint_ids=complaint_ids,
        cited_ids=lambda r: r.addresses_finding_ids,
    )


async def generate_feature_gaps(
    findings: list[Finding], *, ctx: LLMContext
) -> FeatureGapsResponse | None:
    if not _can_possibly_satisfy(findings):
        return None
    all_ids = {f.id for f in findings}
    complaint_ids = {f.id for f in findings if f.kind is FindingKind.PAIN_POINT}
    base_variables = {"findings": _findings_block(findings), "repair_note": ""}
    return await _generate_with_repair(
        FeatureGapsResponse,
        GAPS_PROMPT_ID,
        base_variables,
        ctx=ctx,
        all_ids=all_ids,
        complaint_ids=complaint_ids,
        cited_ids=lambda r: [fid for g in r.gaps for fid in g.addresses_finding_ids],
    )


async def generate_risks(findings: list[Finding], *, ctx: LLMContext) -> RisksResponse | None:
    if not _can_possibly_satisfy(findings):
        return None
    all_ids = {f.id for f in findings}
    complaint_ids = {f.id for f in findings if f.kind is FindingKind.PAIN_POINT}
    base_variables = {"findings": _findings_block(findings), "repair_note": ""}
    return await _generate_with_repair(
        RisksResponse,
        RISKS_PROMPT_ID,
        base_variables,
        ctx=ctx,
        all_ids=all_ids,
        complaint_ids=complaint_ids,
        cited_ids=lambda r: [fid for risk in r.risks for fid in risk.addresses_finding_ids],
    )


async def synthesise(findings: list[Finding], *, ctx: LLMContext) -> SynthesisResult:
    mvp = await generate_mvp(findings, ctx=ctx)
    feature_gaps = await generate_feature_gaps(findings, ctx=ctx)
    risks = await generate_risks(findings, ctx=ctx)

    omitted = []
    if mvp is None:
        omitted.append("mvp_synthesis")
    if feature_gaps is None:
        omitted.append("feature_gaps_synthesis")
    if risks is None:
        omitted.append("risks_synthesis")

    return SynthesisResult(
        mvp=mvp, feature_gaps=feature_gaps, risks=risks, omitted_sections=tuple(omitted)
    )


__all__ = [
    "MIN_FINDINGS_REFERENCED",
    "FeatureGapsResponse",
    "GeneratedStatement",
    "MVPResponse",
    "RisksResponse",
    "SynthesisResult",
    "generate_feature_gaps",
    "generate_mvp",
    "generate_risks",
    "synthesise",
]
