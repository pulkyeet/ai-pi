# AI Product Investigator — Web

Next.js App Router frontend for [Phase 13](../docs/execution_phases/phase-13-frontend.md). Talks to
the FastAPI backend in `src/api/web/` over plain HTTP + SSE — there is no Next.js server-side API
layer of its own.

## Setup

```bash
npm install
cp .env.local.example .env.local   # fill in NEXT_PUBLIC_API_BASE_URL / SUPABASE_URL / SUPABASE_ANON_KEY
npm run dev
```

## Commands

| Command | Does |
|---|---|
| `npm run dev` | Local dev server |
| `npm run build` | Production build — statically renders `/` (homepage) and `/new` at build time |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint (`eslint-config-next`, flat config) |
| `npm test` | Vitest unit suite (`tests/unit/`) — no backend required |
| `npm run e2e` | Playwright suite (`tests/e2e/`) — builds+starts the app itself; see below |

## Architecture notes

- **Homepage is statically rendered** (`app/page.tsx`, `export const dynamic = "force-static"`):
  `getBenchmarkReports()` runs once at `next build` time and the result is baked into HTML — zero
  runtime dependency on the backend or Supabase (phase doc's "no backend dependency" exit
  criterion, and what makes Supabase's 7-day idle-pause risk irrelevant for a logged-out visitor).
  `/r/[runId]` and `/new` are client-rendered instead — a permalink may point at a private run,
  whose auth is a browser-session concern, not a build-time one.
- **SSE is fetch-based, not `EventSource`** (`lib/sse.ts`): `GET /runs/{id}/events` needs an
  `Authorization: Bearer` header for a non-public run, which `EventSource` cannot send. Reconnect
  with `Last-Event-ID` is therefore explicit code here, not an opaque browser behaviour — see that
  file's own tests.
- **The span-offset conversion is the one real trap** (`lib/span.ts`'s `cpToUtf16`): claim spans are
  Python code-point offsets; JS string indices are UTF-16 code units. Silently skipping the
  conversion is invisible on ASCII fixtures and wrong on the first page with an emoji — see the
  property test in `tests/unit/span.test.ts`.
- **`MVP`/`Risk`/`FeatureGap` cite a `findings.id`, not a `claims.id`.** `CitedFinding`
  (`components/CitedSentence.tsx`) resolves that one extra hop via `GET /runs/{id}/findings/{id}`
  (added to the backend in this phase — Phase 12's endpoint list didn't have it) before opening the
  same `SourcePanel` any other citation does.

## Testing without a live backend

This repo's sandbox has no reachable Postgres/Supabase/FastAPI process, so the E2E suite
(`tests/e2e/`) never talks to the real backend:

- `tests/e2e/mock-server.ts` is a small real Node `http` server standing in for the FastAPI
  backend — `playwright.config.ts`'s `webServer` starts it *before* `next build`, because the
  static homepage's server-side fetch happens inside the build process, which `page.route`
  (a browser-only network hook) can never see.
- Every other endpoint (run creation, polling, SSE) is mocked per-test via `page.route` in
  `tests/e2e/fixtures.ts`, so each test can parametrise run status / SSE script independently.
- The one authenticated flow (`tests/e2e/live-run.spec.ts`) seeds a Supabase session directly into
  the `sb-<project-ref>-auth-token` `localStorage` key `@supabase/supabase-js` v2 reads on init,
  since there is no real Supabase project here to run an actual OAuth redirect against. It exercises
  the real `getSession()` code path with a planted session instead of one OAuth produced — the
  closest honest substitute available in this environment. Re-verify against a real Supabase project
  and real Google/GitHub OAuth before trusting this flow in production.
- Only Chromium is installed (`playwright install webkit` needs system libraries this sandbox has no
  root to install) — the `mobile-chrome` project (`devices["Pixel 7"]`) stands in for the
  mobile-viewport checks instead of WebKit's `devices["iPhone 14"]`. Swap back to WebKit wherever
  `--with-deps` can actually run.
