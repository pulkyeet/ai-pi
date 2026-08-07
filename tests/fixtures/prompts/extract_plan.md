---
id: extract_plan
schema: PlanExtraction
cache_prefix_ends_after: instructions
---
## role
You are a synthetic test prompt used only by Phase 05's own live test suite
(`tests/live/test_llm_gateway_live.py`) — never a real domain prompt (see
`src/api/llm/prompts.py`'s module docstring). It deliberately mirrors the
shape of Phase 01's `spikes/llm_openrouter.py` structured-output spike so
this phase's live schema-violation-rate check is measuring the same kind of
call, now going through the real gateway instead of a raw HTTP script.

## instructions
Extract one pricing plan mentioned in the page text below as JSON matching
the required schema exactly: `plan_name` (string), `price_usd` (number or
null if not stated), `billing_period` (one of "month", "year", "one_time",
"unknown").

## user
Page text follows.
