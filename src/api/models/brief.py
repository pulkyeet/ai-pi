from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchBrief(BaseModel):
    """Stage 0 output: free text -> typed brief with per-field confidence
    (masterplan §3). Low confidence on a plan-changing field triggers a
    disambiguation prompt; everything else is inferred silently.
    """

    category: str
    segment: str
    geography: str
    monetisation_guess: str
    field_confidence: dict[str, float] = Field(default_factory=dict)
