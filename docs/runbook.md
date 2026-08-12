# Runbook — Production Operations

Dated 2026-08-11 (Phase 15). Everything an operator needs to keep
`https://ai-product-investigator.fly.dev` up, cheap, and honest. The deploy
itself is described in the [deploy workflow](../.github/workflows/deploy.yml)
and `fly.toml`/`fly.worker.toml` at the repo root.

## Topology

```
Vercel (free)        Next.js static homepage + SSR live runs
  │  HTTPS
Fly.io (<$10/mo)     ai-product-investigator — two machines, one image
  │                    api machine    (fly.toml,      `python -m api.web.main`)
  │                    worker machine (fly.worker.toml, `python -m api.worker`)
  ▼  direct connection (NOT the pooler)
Supabase (free)      Postgres 16 + Auth (Google/GitHub OAuth) + pgvector
  │
  ▼
Langfuse Cloud (free) traces + cost per run (LLM calls only)
```

- **Everything durable lives off the Fly machines.** Fly filesystems are
  ephemeral by design: Postgres is Supabase, backups run from GitHub Actions
  to R2, traces go to Langfuse. Nothing on a Fly machine is worth backing up.
- **Cost model (Phase 14 measured, `docs/benchmark.md`):** ~$0.062 mean / ~$0.078
  p95 per run; two `shared-cpu-1x` Fly machines ≈ $6–8/mo; Supabase/Vercel/
  Langfuse free. Fixed cost ≤ $10/mo is the phase doc's exit criterion.

## Where every secret lives

| Secret | Owned by | Set as | Used by |
|---|---|---|---|
| `DATABASE_URL` (asyncpg form, `?sslmode=require`, **direct** host `db.<ref>.supabase.co`) | Supabase DB password | Fly secret `DATABASE_URL` (from GitHub Actions secret `SUPABASE_DB_URL_ASYNC_DIRECT`) | api + worker machines |
| `SUPABASE_DB_URL` (libpq form, `?sslmode=require`, **session-pooler** host) | Supabase DB password | GitHub Actions secret | alembic in deploy.yml; keepalive + backup in keepalive.yml |
| `SUPABASE_DB_URL_ASYNC` (asyncpg form, `?ssl=require`, **session-pooler** host) | Supabase DB password | GitHub Actions secret | `api.maintenance` in keepalive.yml |
| `OPENROUTER_API_KEY` | OpenRouter dashboard | Fly secret | api machine LLM calls |
| `EXA_API_KEY` | Exa dashboard | Fly secret | api machine search |
| `GH_TOKEN` → `GITHUB_TOKEN` | GitHub fine-grained PAT | GitHub Actions secret `GH_TOKEN`, forwarded to Fly secret `GITHUB_TOKEN` by deploy.yml | api machine domain retrievers |
| `SUPABASE_URL` / `SUPABASE_JWT_AUDIENCE` | Supabase project | Fly secret / env | api machine JWT verification (JWKS, no secret) |
| `CORS_ALLOW_ORIGINS` | — | Fly env | api machine (JSON list incl. the Vercel origin) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | Langfuse dashboard | Fly secrets | api machine tracer (unset = no-op tracer) |
| Quota knobs (`RUN_BUDGET_*`, `MAX_*`, `EXA_*_CAP_USD`, `GLOBAL_RUNS_PER_DAY`, …) | — | Fly env | api machine (derived values from `docs/tuning.md`) |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare R2 | GitHub Actions secrets | backup job only — **never** the backend |
| `R2_ENDPOINT` / `R2_BUCKET` | Cloudflare R2 | Versioned in `keepalive.yml` | Account endpoint and bucket are non-secret; pinning them avoids a valid token being sent to the wrong R2 account/bucket |
| `FLY_API_TOKEN` | Fly dashboard | GitHub Actions secret | deploy workflow |
| `SUPABASE_JWT_SECRET` | Supabase | local `.env` only | **not read by any code** (JWKS-based verification) |

Two rules that hold everywhere:
1. Real values live in Fly secrets and GitHub Actions secrets, never in the
   repo. `deploy/.env.prod.example` and `.env.example` are placeholder
   checklists only.
 2. The Supabase DB password ships in three connection strings. Each has its
    own exact spelling, verified empirically on 2026-08-11:
    - `SUPABASE_DB_URL` — libpq `?sslmode=require`, session-pooler host (CI alembic/psql/pg_dump)
    - `SUPABASE_DB_URL_ASYNC` — asyncpg `?ssl=require`, session-pooler host (CI `api.maintenance`)
    - `SUPABASE_DB_URL_ASYNC_DIRECT` — asyncpg `?sslmode=require`, **direct** host (Fly api/worker)
    Two hosts because the Fly runtime uses the direct host (IPv6) while IPv4-only
    CI uses the session pooler (rule 4); two dialects on the pooler because
    asyncpg and libpq spell TLS differently there; but on the **direct** host
    asyncpg must use the `sslmode` spelling too — the direct endpoint rejects
    `?ssl=require` with `CantChangeRuntimeParamError` (crash observed on the
    first Fly deploy). Keep all three GitHub secrets in sync when the password
    rotates.
3. GitHub Actions reserves the secret name `GITHUB_TOKEN` (that name is its
   auto-generated per-run token), so the Actions secret is named `GH_TOKEN`.
   `deploy.yml` forwards it to the app's `GITHUB_TOKEN` env var, which is what
   `config.py` reads — if the Actions secret is ever renamed, update deploy.yml
   to match.
4. **Direct on Fly, pooler in CI — and never the pooler for the app.** The
   pooler's session mode caps clients at `pool_size: 15`; the app's asyncpg
   pool is 10 connections per machine (two machines ⇒ 20+), so pointing the
   Fly runtime at the pooler **crashes startup with `EMAXCONNSESSION`**
   (observed 2026-08-11, first Fly deploy). The Fly machines therefore use
    the **direct** connection (`db.<ref>.supabase.co`, asyncpg `?sslmode=require`),
   which is IPv6-only but Fly has IPv6. IPv4-only CI (alembic, keepalive
   psql, `pg_dump`, `api.maintenance` — one to ten short-lived connections
   each) uses the **session pooler**
   (`aws-0-ap-south-1.pooler.supabase.com:5432`, user `postgres.<ref>`), which
   pins a server connection per client. The direct host is unreachable from
   this machine and GitHub Actions runners; the pooler is unreachable for the
   app's pool size. This supersedes the phase doc's "direct connection, not
   the pooler" note (dated 2026-08-11). Verified: migration chain 0001→0012
   applied cleanly to the real Supabase project over the pooler on 2026-08-11.

## The nine alerts

Checked against `GET /metrics` (authenticated endpoint — a Supabase JWT in an
`Authorization: Bearer` header) by an operator; threshold values live as named
constants in `src/api/web/routes/metrics.py`. **No external alerting service** —
"alerting" means the runbook's check-the-metrics habit plus GitHub's native
failed-workflow notification for the keepalive/backup/maintenance crons.

| # | Metric | Threshold | Level | First action |
|---|---|---|---|---|
| 1 | Runs per day | approaching `GLOBAL_RUNS_PER_DAY` (4) | degradation | Watch; the kill switch trips at the cap anyway |
| 2 | Cost per run (rolling mean, 30d) | > 2× Phase 14 baseline (**$0.124** vs $0.0621) | degradation | Investigate before it compounds |
| 3 | Search spend month-to-date | > 70% of the **$10/mo Exa allowance** (**$7.00**) | degradation | Reduce `GLOBAL_RUNS_PER_DAY`; see "Cap hit" |
| 4 | Sentence binding rate | **< 100%** | **PAGE — immediately** | The product claim is false in production; see below |
| 5 | Extraction drop rate | > 1.5× baseline (**0.30** vs 0.20) | degradation | Check `run_stats` drift; see "Binding/drop" |
| 6 | p95 run latency | > `RUN_TIMEOUT_S` × 0.8 (**512s** at 640) | degradation | The run-timeout cap is now the binding constraint |
| 7 | Task failure rate | > 2× baseline (**0.10** vs 0.05) | degradation | Identify the failing task kind |
| 8 | Database size | > 70% of 500 MB (**350 MB**); critical 85% (**425 MB**) | degradation | Nightly eviction should be handling it; see "DB near limit" |
| 9 | Keepalive job | any failure | **PAGE** (GitHub failed-workflow notification) | A paused database is the worst available failure |

Baselines are the Phase 14 measured numbers (`docs/benchmark.md`, dated
2026-08-08) unless otherwise noted; drop-rate baseline is an operational
default (0.20) until a real production baseline accumulates.

## Alert 4 / 5 — binding rate and extraction drops

Binding below 100% means a sentence shipped without a verbatim span, which
violates the masterplan's Rule 1 — the product's core claim. Before anything
else: pull the offending run (`python -m api.cli inspect <run_id>` from a
checkout) and find which sentence/claim. The most likely causes are an
`extractor_version` drift (prompt or model changed → old cached extractions
re-bound against different text — expected and benign) or a normalisation
contract change in `api.retrieval.extract_text`. The drop rate is read from
the `run_stats` table (written at run-finish since Phase 15, migration `0012`);
a rising `quote_not_in_source`/`quote_ambiguous` share points at
extractor/prompt compliance (see `docs/tuning.md` §7, the 06a drop trace —
HTML-entity mismatch and text-where-typed dominate, not short quotes).

## Cap hit (per-run budget, daily quota, or Exa allowance)

Layered caps, from the inside out (`masterplan §8.2/§8.3`, all derived in
`docs/tuning.md`):

1. Per-run budget weight (70) and dollar cap ($0.25) — bug insurance.
2. Per-user (3/day) and global (4/day) daily quotas — the wall-clock gate.
3. Kill switch on global cap exhaustion — serves benchmark reports only,
   `GET /health` reports `kill_switch_enabled=true`, live runs resume after
   `system_state` is reset by an operator.
4. **Vendor-side hard caps.** OpenRouter has a spend limit set in its
   dashboard. **Exa does not offer a dashboard spend cap** — the $10/mo
   recurring allowance is the ceiling, and the application-level
   `search_credit_usage` ledger with `EXA_DAILY_CREDIT_CAP_USD=0.33`
   (`EXA_GLOBAL_DAILY_CREDIT_CAP_USD=0.33`) is what enforces it.
   **Documented decision (2026-08-11): treat the Exa allowance itself as the
   hard cap** — there is no vendor-side ceiling to set, so the ledger is the
   enforcement layer. This is a deliberate, stated gap, not a silent one.
5. Spend alerting at the thresholds above.

**When the cap hits:** users see `quota_exceeded` (429) or
`live_runs_paused` (503). Do **not** raise the caps to make the site work —
a cap that fires is the system doing its job. First check whether spend is
legitimately up (a pricing-page crawl got heavier, a new category needs more
searches) before touching a number; any change goes through `docs/tuning.md`
with a date and a reason.

## Vendor outage

Every provider boundary degrades rather than crashes (Phase 03–05 design):
Exa failures turn into degraded `SearchResponse`s, domain retrievers raise
`RetrieverUnavailableError` into a `task.failed`, Langfuse is a no-op tracer
when unreachable. A vendor outage therefore reads as a **rise in task-failure
rate (alert 7) or search degradation**, not a down site.

| Vendor down | Symptom | Action |
|---|---|---|
| Exa | discovery/fetch tasks fail; search `degraded` | Nothing — runs still complete on cached/snippet/domain-retriever paths. `tests/live/test_vendors.py` drift check tracks recovery |
| OpenRouter | extraction/planning tasks fail | Runs fail at the LLM stage; reports ship partial. Check the model still exists / the key hasn't been rate-limited |
| GitHub API | github tasks fail | `RetrieverUnavailableError`, covered; categories that need OSS discovery lose a channel |
| Langfuse | tracer errors | Silent no-op by design — **never** blocks a run |

`tests/live/test_vendors.py` (nightly) and the Phase 14 benchmark regression
(`.github/workflows/bench.yml`) are the drift canaries.

## DB near limit (alert 8)

The 500 MB Supabase ceiling is managed actively:

- **Nightly eviction** (keepalive workflow, `python -m api.maintenance`):
  expired unpinned `sources.extracted_text` is nulled (`api.retrieval.cache
  .evict_expired` — the row and its `claims.quote_context` survive, so
  drill-down still works), `run_events` are pruned past 30 days, and benchmark
  sources are pinned (`sources.is_pinned=true` so they never evict).
- **Monitored size:** `GET /metrics` reports `db_size_bytes` and the
  percentage of 500 MB; alerts at 70% (350 MB) and 85% (425 MB).

**When alert 8 fires:** check `GET /metrics`, then `SELECT count(*) FROM
sources WHERE is_pinned` and the pinned benchmark set. Expected steady-state:
benchmark sources ~12 MB pinned + per-run ~0.40 MB (measured 2026-08-10,
`docs/working_knowledge.md`), which the nightly eviction keeps bounded. If the
size keeps climbing with eviction running, the eviction job itself is the
first suspect (it runs from GitHub Actions — a failing workflow silently means
no eviction; check alert 9's sibling jobs).

## Backup & restore

Nightly `pg_dump` from GitHub Actions (`deploy/backup.sh`) → gzipped → R2
bucket `ai-pi-backups`, 30 dumps retained. Never from a Fly machine
(ephemeral FS).

**Restore (tested monthly per the phase doc):** into a fresh Supabase project
(or a local Postgres):

```bash
# 1. Fetch the dump
aws s3 cp "s3://ai-pi-backups/<OBJECT>.sql.gz" ./ --endpoint-url "$R2_ENDPOINT"
gzip -d <OBJECT>.sql.gz

# 2. Load into a scratch database (NEVER the live project)
psql "$SCRATCH_DB_URL" -f <OBJECT>.sql

# 3. Verify — row counts and a sample report render
psql "$SCRATCH_DB_URL" -tAc \
  "SELECT count(*) FROM runs; SELECT count(*) FROM claims; SELECT count(*) FROM reports;"
```

The monthly restore is an Ops item in the phase doc's testing table; schedule
it as a manual calendar task (a cron that restores into a scratch DB is a
second paid Supabase project — not free, so not automated).

**Pitfall:** the dump restores the full schema including the local
`auth.users` stub rows if the dump predates the real Supabase migration — fine
for a scratch DB, meaningless otherwise. `user_profiles` FKs resolve against
real `auth.users` only on the real project (verified pre-deploy, Phase 15).

## Rollback

The deploy workflow deploys in order **migrations → worker → API** and runs an
explicit health check; on failure it re-deploys the previously captured API
image. Migrations are written backward-compatible for one version so a
rollback never strands the schema.

**Manual rollback** (deploy workflow failed to):

```bash
flyctl machine list -a ai-product-investigator
flyctl machine update <api-machine-id> \
  --image registry.fly.io/ai-product-investigator:<previous-image-tag> --yes
curl -fsS https://ai-product-investigator.fly.dev/health
```

**If the rollback lands on a pre-migration image:** because migrations are
additive and backward-compatible (0012's `run_stats` is written only by the
new image and read only by `GET /metrics`; the old image neither writes nor
reads it), old code runs fine against the new schema. Only `downgrade` paths
would change behavior, and those are never auto-run.

## Key rotation

| Key | How | Repercussion |
|---|---|---|
| Supabase DB password | Supabase dashboard → rotate; update all three GitHub secrets `SUPABASE_DB_URL`, `SUPABASE_DB_URL_ASYNC`, `SUPABASE_DB_URL_ASYNC_DIRECT`, then re-push `fly secrets set DATABASE_URL` | None to running machines until they reconnect (pool reconnects) |
| OpenRouter / Exa / GitHub keys | Vendor dashboard → rotate → `fly secrets set OPENROUTER_API_KEY=…` (new value) → `fly machines restart` (for the GitHub key also update Actions secret `GH_TOKEN`) | A bad key degrades the relevant task class, never crashes the site |
| Langfuse | `fly secrets set LANGFUSE_*` | None — unset keys are a no-op tracer |
| `FLY_API_TOKEN` | Fly dashboard → GitHub Actions secret | Deploys stop until replaced |
| `R2_*` | Cloudflare → GitHub secrets | Backups fail (alert 9 sibling) until replaced |

Fly secrets are live-updated per machine; a restart applies them to the
process (env is baked at process start — always restart after a secret change).

## Manual checks (suggested cadence)

- **Daily:** GitHub Actions → keepalive/maintenance/backup jobs green (alert 9).
- **Weekly:** `curl -H "Authorization: Bearer <your-jwt>" https://ai-product-investigator.fly.dev/metrics` and eyeball the nine thresholds; confirm the homepage benchmark reports still load logged-out.
- **Monthly:** restore a backup into scratch and verify (above); confirm the
  R2 bucket's 30-dump retention looks sane.
