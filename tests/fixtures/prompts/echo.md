---
id: echo
schema: EchoResult
cache_prefix_ends_after: instructions
---
## role
You are a synthetic test prompt used only by Phase 05's own test suite —
never a real domain prompt (see `src/api/llm/prompts.py`'s module
docstring; extraction/planning/synthesis prompts belong to Phases 06/09/11).

## instructions
Given the user's message and any untrusted content, return JSON matching
the required schema exactly. This prompt exists purely to exercise
`PromptRegistry`, `render_messages`, and `structured()` mechanically.

## user
Echo the value: {{message}}
