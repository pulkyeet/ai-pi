# Phase 06 extraction fixture corpus

Each fixture is three files sharing a stem: `<name>.txt`, `<name>.llm.json`,
`<name>.expected.json`.

- `<name>.txt` — the page's **already-extracted, normalised text**
  (`source.extracted_text`), not raw HTML. Phase 03 owns HTML→text
  extraction and already has its own fixture corpus
  (`tests/fixtures/pages/*.html`) exercising trafilatura against real
  vendor pages; this phase's contract starts one step later, at
  `extract_claims(source) -> ExtractionResult`, so re-deriving text from
  HTML here would just make these fixtures depend on trafilatura's output
  for no benefit — text-extraction quality isn't what this corpus tests.
- `<name>.llm.json` — a **committed, fake LLM response**: `{"claims": [...]}`
  in exactly the shape `api.extract.validate.ExtractionResponse` validates.
  Standing in for a real model call, so the corpus runs offline and
  deterministically, per the phase doc's own testing table ("Fixture pages →
  expected claims ..., from committed LLM responses").
- `<name>.expected.json` — `{"claims": [...], "drop_reason_counts": {...}}`.
  Each expected claim lists `attribute`/`value_text`/`value_num`/`quote` —
  the set of claims that must survive to `ExtractionResult.claims` (spans
  aren't duplicated here; they're asserted separately by re-locating `quote`
  in the fixture's own `.txt`). `drop_reason_counts` maps each `DropReason`
  value that should fire to how many times. Assertions in
  `tests/integration/test_extractor.py` compare claims as an unordered set
  of content tuples, never ordering or exact list length — the phase doc's
  own rule for why fixture assertions must be content-based.
