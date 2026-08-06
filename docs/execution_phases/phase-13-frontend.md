# Phase 13 — Frontend & Drill-Down UI

| | |
|---|---|
| **Depends on** | [12](phase-12-api-auth-quotas.md) |
| **Unlocks** | [15](phase-15-deployment-observability.md) |
| **Milestone** | No |
| **Concrete output** | A deployed-ready web app where clicking any sentence opens a source panel with the exact character span highlighted |

---

## Objective

Build the surface that makes the evidence chain visible — because an invisible guarantee is indistinguishable from no guarantee.

## Why the drill-down is the whole UI

Masterplan §11: *"report view with drill down panel — **drill down is the demo**."*

The system's differentiating claim is that no sentence can be published unless it is bound to a specific character span in a specific fetched page. Every other AI research tool produces confident prose. The only way a visitor can tell this one apart in ten seconds is by clicking a sentence and watching the source open with the exact quote highlighted.

So the drill-down is not a feature of the report view. It is the point of the report view, and everything else is arranged around it.

---

## Scope

### In

- Next.js App Router application
- Homepage: statically rendered benchmark reports
- Auth via Supabase client
- Query input with disambiguation chips
- Live plan checklist over SSE
- Report view with progressive finding arrival
- **Drill-down panel with span highlighting**
- Contradiction, freshness and coverage display
- Permalinks and JSON export

### Out

- Deployment ([Phase 15](phase-15-deployment-observability.md))
- Team accounts, sharing, saved workspaces — explicitly out of scope per masterplan §2

---

## Deliverables

```
web/
├── app/
│   ├── page.tsx                    # homepage: benchmark reports
│   ├── r/[runId]/page.tsx          # report view
│   └── new/page.tsx                # query input + live run
├── components/
│   ├── PlanChecklist.tsx
│   ├── ReportView.tsx
│   ├── CitedSentence.tsx
│   ├── SourcePanel.tsx             # the demo
│   ├── SpanHighlight.tsx           # the critical component
│   ├── ContradictionCard.tsx
│   ├── CoverageBanner.tsx
│   └── DisambiguationChips.tsx
├── lib/
│   ├── api.ts
│   ├── sse.ts                      # reconnect + Last-Event-ID
│   └── supabase.ts
tests/
├── unit/                           # vitest
└── e2e/                            # Playwright
```

---

## Design

### Homepage — statically rendered

The ten benchmark reports are the homepage (masterplan §12.13: one artifact serves quality measurement, instant recruiter demo, and the logged-out reading experience).

Rendered **statically at build time**, which matters for two reasons beyond speed:

1. It is the recruiter-facing surface, and it must load instantly with full drill-down and zero backend dependency.
2. It makes the Supabase 7-day idle-pause risk irrelevant for public visitors — the homepage never touches Postgres. See [README](README.md#sixth-change-supabase-over-neon).

Full drill-down works logged out, on static pages. Every span, every source, every contradiction.

### The span highlight — and the one real trap

`SpanHighlight` renders source text with `[char_start, char_end)` highlighted, using `quote_context` + `context_offset` when full source text has been evicted ([Phase 03](phase-03-fetch-source-cache.md)).

**The trap: Python and JavaScript disagree about string indices.**

Python string indices are **code points**. JavaScript string indices are **UTF-16 code units**. For any text containing a character outside the Basic Multilingual Plane — an emoji, some CJK extensions, mathematical symbols — a Python offset of 10 is not a JavaScript offset of 10, and the highlight lands in the wrong place.

This is silent: it works perfectly on ASCII pricing pages and breaks on the one page with an emoji in the header. Since spans are the product's core claim, a visibly wrong highlight is worse than no highlight.

Resolution: convert explicitly at the boundary.

```ts
// Python code-point offset -> JS UTF-16 offset
function cpToUtf16(text: string, cpIndex: number): number {
  return [...text].slice(0, cpIndex).join("").length;
}
```

Tested with an emoji-containing fixture. This is called out here because it is exactly the kind of defect that survives every test written against English marketing copy.

### The drill-down interaction

```
Report sentence  →  click  →  panel slides in
                              ├─ source URL, fetched date, grade badge
                              ├─ source text with the span highlighted, scrolled into view
                              ├─ confidence with its inputs shown
                              └─ "other claims from this source"
```

Showing the **confidence inputs** — grade, distinct domains, age, contradiction status — is worth the space. It turns a number into an argument, which is the entire justification for computing it rather than generating it (masterplan §12.5). A visitor who sees `0.61 = 0.75 (grade B) × 1.05 (2 domains) × 0.94 (86 days) × 1.0` understands immediately that the score means something.

Multi-cite sentences show a citation count; the panel offers navigation between sources.

### Live run experience

```
plan.created    → checklist renders, all items pending
task.started    → item spins
task.completed  → item ticks green
task.failed     → item marks amber (not red — a dead branch is normal)
finding.added   → finding streams into the report area
report.ready    → full report renders
```

Amber rather than red for failures is deliberate: partial failure is the normal case (masterplan §4.2), and a red X trains the user to read a successful run as broken.

The SSE client handles reconnect with `Last-Event-ID` ([Phase 12](phase-12-api-auth-quotas.md)) — a dropped mobile connection mid-run must not lose the run.

### Disambiguation chips

At most two, best guess pre-selected, ignorable (masterplan §3):

```
[ B2B ✓ | B2C ]     [ Global ✓ | India | US ]     [ Go ]
```

Pressing Go without touching them uses the pre-selected guesses. The chips must never feel like a required form — they are an offer, and the default path is to ignore them.

### Coverage and freshness, displayed honestly

Masterplan Rule 4: a run whose funding branch died says so, out loud, on the report.

So a coverage banner sits above the report, not in a footer: `Coverage 82% — funding data unavailable for this category`. Freshness renders as `Median source age: 41 days · Oldest: 2025-03-11`.

Contradictions get their own card showing both values, both grades, both dates, and which won. This is a feature, not an apology — "pricing page says $5 last week, a 2025 review says $18" is genuinely useful signal (masterplan §4.7), and presenting it confidently is what distinguishes the product from one that silently picked a number.

### Performance

- Static homepage, no client-side data fetching for benchmark reports
- Source text loaded on demand when a panel opens, not with the report
- Virtualised rendering for long source documents
- Report JSON cached client-side for the session

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | `SpanHighlight` renders correct range for ASCII | Basic correctness |
| Unit | **`SpanHighlight` with emoji, CJK, and mathematical symbols** | Code-point → UTF-16 conversion correct |
| Unit | `cpToUtf16` matches Python semantics — property test over generated Unicode strings | The offset contract |
| Unit | Highlight from `quote_context` + `context_offset` when source evicted | Fallback path |
| Unit | Span at text start, at end, spanning the whole text | Boundaries |
| Unit | SSE client reconnect: resumes from `Last-Event-ID`, no duplicate rendering | Mobile case |
| Unit | Coverage banner renders failed branches; hidden at 100% | Rule 4 |
| Unit | Contradiction card shows both values with grades and dates | Losers surfaced |
| E2E | Homepage loads; benchmark report readable **logged out** | Public surface |
| E2E | **Click a sentence → panel opens → highlighted text equals the claim's quote** | The demo, asserted |
| E2E | Drill-down works logged out on a static page | Recruiter path |
| E2E | Login via Supabase → submit query → chips appear → Go → checklist ticks → report renders | Full journey |
| E2E | Task failure renders amber, run still completes | Partial failure UX |
| E2E | Network interruption mid-run → reconnect → run completes | Resilience |
| E2E | JSON export downloads and matches the API payload | Export |
| E2E | Permalink loads a completed run | Sharing |
| A11y | Keyboard navigation reaches drill-down; panel is focus-trapped and escapable; axe passes on report view | Usable without a mouse |

The emoji span test and the click-to-highlight E2E test are the two that guard the product claim. The first prevents a silent correctness bug; the second proves the claim end to end through the real stack.

---

## Exit criteria

- [ ] Homepage statically renders all ten benchmark reports, no backend dependency
- [ ] Full drill-down works logged out
- [ ] **Clicking any sentence opens a panel with the exact span highlighted** — E2E verified
- [ ] Code-point → UTF-16 conversion correct, proven with non-BMP fixtures
- [ ] Eviction fallback via `quote_context` renders correctly
- [ ] Confidence inputs displayed, not just the score
- [ ] Live checklist ticks in real time; findings stream progressively
- [ ] SSE reconnect resumes without loss or duplication
- [ ] Failures render amber; run completes visibly
- [ ] Disambiguation chips ignorable with sensible defaults
- [ ] Coverage banner prominent; freshness shown; contradictions carded
- [ ] Permalink and JSON export work
- [ ] Keyboard accessible; axe clean on the report view
- [ ] Full E2E suite green against a locally-running API

---

## Risks

| Risk | Mitigation |
|---|---|
| **Span highlight silently wrong on non-ASCII** | Explicit code-point conversion plus property test plus non-BMP fixtures. Called out as the phase's primary technical risk because it is invisible to English-only testing. |
| Two-minute wait feels broken | Live checklist plus progressive findings, exactly as masterplan §4.10 prescribes. E2E test observes real intermediate states rather than only the end state. |
| Homepage slow, recruiter bounces | Static rendering, no client fetch, no auth. The single most important performance path in the product. |
| Supabase pause breaks the homepage | It cannot — the homepage is static and touches no database. This is a designed-in mitigation, not a hope. |
| Drill-down panel unusable on mobile | Full-screen sheet on small viewports rather than a side panel; E2E runs at mobile viewport. |
| Long source documents freeze the panel | Virtualised rendering; tested with a 500 KB fixture. |
| Report JSON grows too large for the client | Findings paginate if needed; measured against real benchmark reports in [Phase 14](phase-14-benchmark-calibration.md). |

## Open decisions

1. **Highlight granularity for multi-cite sentences.** One sentence may cite three claims across three sources. Show the first and let the user page through, or show all three stacked? Lean first-plus-navigation, since stacking makes the panel busy and dilutes the "here is the exact quote" moment that sells the product.
2. **Should low-confidence findings be visually de-emphasised or filtered?** Carried from [Phase 11](phase-11-synthesis-report-assembly.md). Leaning de-emphasis — they carry real citations, and hiding evidence undercuts the transparency the product is built on.
3. **Framework check.** Next.js App Router per masterplan §11. Worth a moment's thought at the start: nearly all of this is static rendering plus one SSE-driven page, which a lighter setup would also serve. Sticking with Next unless there is a concrete reason not to — Vercel's free tier and its static rendering are a good fit, and deviating from the masterplan needs a better reason than taste.
