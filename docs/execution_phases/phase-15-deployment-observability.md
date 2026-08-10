# Phase 15 — Deployment, Observability & Cost Control

| | |
|---|---|
| **Depends on** | [12](phase-12-api-auth-quotas.md), [13](phase-13-frontend.md), [14](phase-14-benchmark-calibration.md) |
| **Unlocks** | — |
| **Milestone** | ⭐ **Shipped** |
| **Concrete output** | A live public URL, running at **~$4.30/month fixed and ~$0.04/run**, with traces, spend caps, and alerts that fire before a bill does |

---

## Objective

Get it running in public, cheaply, with enough instrumentation to explain any cost or quality change after the fact.

## What the masterplan got wrong, and what actually changed

Masterplan §11 specifies "Fly.io app plus worker, Neon or Supabase Postgres" fitting a "near zero cost constraint". Fly.io removed its permanent free tier in 2024 ([D4](README.md#deviations-from-the-masterplan)), so "near zero cost" was never achievable there.

**The target did not change — the cost claim did.** Fly remains the deployment platform, now on measured evidence rather than assumption: this account already runs a comparable workload for under $5/month. A cheaper VPS was considered and rejected, because saving a dollar or two is not worth hand-rolling host maintenance, TLS, and deploy tooling that Fly already provides on a platform that is known-working here.

So: Fly for API and worker, Vercel for the frontend, Supabase for Postgres and Auth. A few dollars a month, and the "near zero cost" language in the masterplan should be read as "small and fixed", not "free".

---

## Scope

### In

- Production topology and provisioning
- Supabase project setup, migrations, keepalive
- Docker images and Compose deployment
- CI/CD
- Backups and restore verification
- Langfuse tracing, structured logs, metrics
- Spend caps, alerts, kill-switch operation
- Benchmark report publication as the static homepage
- README with the architecture and honest quality numbers

### Out

- Autoscaling, multi-region, high availability. This is a portfolio artifact with a bounded budget; engineering for scale it will not see is the wrong trade.

---

## Deliverables

```
fly.toml                       # api machine
fly.worker.toml                # worker machine, same image, different entrypoint
deploy/
├── .env.prod.example
└── backup.sh
Dockerfile                     # api + worker, multi-stage
docker-compose.yml             # local dev only — Postgres + pgvector
.github/workflows/
├── deploy.yml
├── keepalive.yml              # Supabase idle-pause guard
└── bench.yml
docs/runbook.md
README.md                      # architecture + benchmark numbers
```

---

## Design

### Topology

```
Vercel (free)          ── Next.js, static benchmark reports, SSR for live runs
        │
        ▼  HTTPS
Fly.io (<$5/mo)        ── FastAPI machine   ─┐
                          worker machine    ─┤ same image, different entrypoint
        │                                     │
        ▼                                     │
Supabase (free)        ── Postgres + Auth ◄───┘
        │
        ▼
Langfuse Cloud (free)  ── traces, cost per run
```

**Two machines, one image, one managed database.** The API and worker share a Dockerfile and differ only by entrypoint, so there is exactly one thing to build and one thing to version. Fly terminates TLS, so no reverse proxy of our own.

**Why two machines rather than two processes on one.** The worker does long, bursty, CPU-heavy work (crawling, extraction); the API must stay responsive to serve SSE. Separating them means a heavy run cannot starve the API, and each can be sized and restarted independently. The executor's leasing design ([Phase 02](phase-02-executor-core.md)) already assumes workers are separate processes that can die and be replaced, so this costs nothing architecturally.

Docker Compose survives for **local development only** — Postgres with pgvector, matching [Phase 00](phase-00-foundation-contracts-ci.md). It is not a deployment mechanism.

### Supabase setup

1. Create the project; capture connection string, anon key, JWT secret
2. Run migrations against it — **the first time the [Phase 00](phase-00-foundation-contracts-ci.md) local `auth.users` stub meets the real schema.** Verify the `user_profiles` FK resolves against real Supabase auth, not the stub.
3. Configure Google and GitHub OAuth providers in the dashboard
4. Enable pgvector
5. Set connection limits appropriate to a long-lived worker (direct connection, not the pooler — the pooler is for serverless, and this is a persistent process)

**The idle-pause guard.** Free projects pause after 7 days idle ([README](README.md#sixth-change-supabase-over-neon)). Two independent mitigations, because a paused database on a recruiter's click is the worst available failure:

1. **Structural** — the homepage is statically rendered on Vercel and touches no database. Public visitors are unaffected by a pause.
2. **Operational** — a GitHub Actions cron every 3 days issues a trivial query. Free, and it means the project never reaches the idle threshold.

The cron is monitored: a failed keepalive alerts, because a silently broken keepalive plus a quiet fortnight is exactly how the pause happens anyway.

### Storage management

The 500 MB ceiling needs active management, and [Phase 14](phase-14-benchmark-calibration.md) has by now measured real bytes per run.

- Nightly TTL eviction of unpinned `sources.extracted_text` ([Phase 03](phase-03-fetch-source-cache.md))
- Benchmark sources pinned, never evicted (~12 MB)
- Event pruning past a retention window ([Phase 02](phase-02-executor-core.md))
- A monitored size query; alert at 70% and 85% of the ceiling

Drill-down survives eviction via `quote_context` ([Phase 00](phase-00-foundation-contracts-ci.md), [Phase 03](phase-03-fetch-source-cache.md)) — and there is a production check that verifies this on real evicted data, not only in tests. A mitigation that has never been exercised against production data is a hypothesis.

### Deployment

Multi-stage Dockerfile, non-root user, pinned base image digest. Both machines run the same image with a health check; Fly restarts on failure.

CI/CD: on push to `main`, run `make check`, build, then `fly deploy` for the API and worker machines, health check, roll back on failure. Migrations run before the new image starts, and are backward-compatible for one version so a rollback does not strand the schema.

**Deploy order matters.** The worker and API share a schema, so deploy migrations first, then the worker, then the API — a worker running old code against a new schema is the failure mode backward-compatible migrations are for. Verify a rollback actually works before relying on it; an untested rollback is the same kind of belief as an untested backup.

### Backups

Supabase free tier does **not** include automated backups. So: a nightly `pg_dump`, gzipped and pushed to object storage. Run it from a GitHub Actions cron rather than from a Fly machine — Fly machines have ephemeral filesystems, so a dump written locally is a dump that disappears, and the backup job has no reason to be coupled to app uptime. The same workflow file can carry the keepalive query.

**Restore is tested, not assumed** — a monthly restore into a scratch database, verified by row counts and a sample report render. An untested backup is a belief, not a backup.

### Observability

**Traces** — Langfuse on every LLM call ([Phase 05](phase-05-llm-gateway.md)). Free Hobby tier is 50,000 units/month ≈ **770 runs/month**, well above expected volume. Per-run cost is visible per trace, which is what makes a cost regression diagnosable rather than merely noticeable.

**Logs** — structured JSON, `run_id` and `task_id` on every line. A user reporting "my run failed" is traceable from the run id alone.

**Metrics**, exposed on an authenticated endpoint and checked by the runbook:

| Metric | Alert threshold |
|---|---|
| Runs per day | approaching `GLOBAL_RUNS_PER_DAY` |
| Cost per run (rolling mean) | > 2× the [Phase 14](phase-14-benchmark-calibration.md) baseline |
| Search spend month-to-date | > 70% of allowance |
| Sentence binding rate | **< 100% — page immediately** |
| Extraction drop rate | > 1.5× baseline |
| p95 run latency | > `RUN_TIMEOUT_S` × 0.8 |
| Task failure rate | > 2× baseline |
| Database size | > 70% of 500 MB |
| Keepalive job | any failure |

Sentence binding below 100% is the only page-immediately alert. Everything else is a degradation; that one is the product claim being false in production.

### Cost control

Layered, so no single failure produces a bill:

1. Per-run budget weight and dollar caps ([Phase 02](phase-02-executor-core.md))
2. Per-user and global daily quotas ([Phase 12](phase-12-api-auth-quotas.md))
3. Kill switch on global cap — serves reports only ([Phase 12](phase-12-api-auth-quotas.md))
4. **Vendor-side hard caps** — OpenRouter spend limit, search provider budget cap, set in each vendor dashboard
5. Spend alerting at 50% / 70% / 90% of monthly budget

Layer 4 is the one that actually guarantees the ceiling. Application-level caps depend on the application being correct; a vendor-side cap holds even if the application is the thing that broke. Both exist because they fail independently.

### The README

The repo is public and is part of the artifact. It needs:

- The architecture, and the decisions from masterplan §12 with their reasoning
- **The injection-resistance argument stated explicitly**, per masterplan §8.3: page content only ever enters a schema-constrained extraction prompt whose output space is the closed claim vocabulary; output failing validation is dropped; a quote not literally present in the source is discarded. There is no path from page text to free-text generation.
- **Honest benchmark numbers** from [Phase 14](phase-14-benchmark-calibration.md), with methodology and the tuning/held-out split
- The deviations from the original plan and why ([README](README.md#deviations-from-the-masterplan)) — a plan that survived contact with reality and adapted is a better story than one that pretends nothing changed
- Self-hosting instructions, since that is the answer to "can I use my own keys"

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Pre-deploy | Migrations apply cleanly to real Supabase, including the real `auth` schema | The stub assumption holds |
| Pre-deploy | Full benchmark against production config | No environment-specific breakage |
| Smoke | Health endpoint; static homepage; drill-down logged out | Public surface |
| Smoke | **Full authenticated run in production** end to end | The system works where it lives |
| Smoke | SSE survives Fly's proxy — no buffering, heartbeats delivered, reconnect works | Proxy behaviour is environment-specific and must be verified live |
| Smoke | OAuth via both Google and GitHub; same email → one account | Supabase linking behaves as the masterplan requires |
| Ops | Restore from backup into a scratch DB; verify row counts and a sample report | Backups are real |
| Ops | Kill switch flips and degrades to read-only, then recovers | Guardrail works in production |
| Ops | Eviction runs; **drill-down still works on an evicted source in production** | The 500 MB mitigation verified on real data |
| Ops | Keepalive cron succeeds and is monitored | Pause guard active |
| Ops | Deploy rollback restores the previous version and passes health | Recovery path |
| Ongoing | Nightly benchmark regression ([Phase 14](phase-14-benchmark-calibration.md)) | Quality does not drift silently |
| Ongoing | Nightly vendor drift tests ([Phase 01](phase-01-dependency-validation-spike.md)) | Cassettes stay honest |

---

## Exit criteria

- [ ] Live public URL, TLS, homepage loads fast and fully static
- [ ] Benchmark reports readable logged out with working drill-down
- [ ] Authenticated run completes end to end in production
- [ ] Both OAuth providers work; cross-provider account linking verified
- [ ] SSE works through Fly's proxy, including reconnect
- [ ] Worker and API deploy as separate machines from one image; a heavy run does not degrade API latency
- [ ] Migrations verified against real Supabase auth schema
- [ ] Keepalive cron active and monitored
- [ ] Eviction running; drill-down verified on a real evicted source
- [ ] Nightly backups; **restore tested and verified**
- [ ] Langfuse tracing live; cost per run visible per trace
- [ ] All nine alerts configured; binding-rate alert pages
- [ ] Vendor-side hard spend caps set in every provider dashboard
- [ ] Kill switch exercised in production and recovered
- [ ] CI/CD deploys on merge with health check and rollback
- [ ] Measured fixed cost ≤ $10/mo; measured cost per run ≤ $0.05
- [ ] README complete: architecture, decisions, injection argument, honest numbers, deviations, self-hosting
- [ ] `docs/runbook.md` covers: cap hit, vendor outage, DB near limit, restore, rollback, key rotation

---

## Risks

| Risk | Mitigation |
|---|---|
| Supabase project pauses and a visitor hits a dead site | Two independent mitigations: static homepage (structural) and monitored keepalive cron (operational). |
| 500 MB reached | Nightly eviction, benchmark pinning, size alerts at 70%/85%, and a drill-down fallback verified against production data. |
| Unexpected bill | Five layers, the outermost being vendor-side hard caps that hold even if the application is what broke. |
| Fly region outage takes the API down | Accepted, deliberately. The recruiter-facing surface is on Vercel and survives it entirely; benchmark reports stay readable. Recovery is a redeploy against a database that lives elsewhere. Multi-region is explicitly out of scope for a portfolio artifact. |
| Fly machine filesystem is ephemeral | Nothing durable is written to it — Postgres is Supabase, backups run from GitHub Actions, and traces go to Langfuse. Worth stating because it is exactly the assumption a VPS-shaped design would have violated. |
| No managed backups on the free tier | Nightly `pg_dump` from GitHub Actions with **tested** monthly restore. |
| Langfuse free tier exhausted | ~770 runs/month headroom; sampling available; self-hosting is MIT-licensed if it ever matters. |
| Vendor changes terms again | Nightly drift tests, dated `docs/external_apis.md`, and abstractions at every vendor boundary ([Phase 04](phase-04-search-domain-retrievers.md), [Phase 05](phase-05-llm-gateway.md)) so a swap is config rather than surgery. |
| Secrets leak in the public repo | Secrets only in `.env` and GitHub Secrets; `.env.prod.example` has placeholders; a CI secret-scan runs on every push. |

## Open decisions

1. **If Supabase becomes a problem, move Postgres to Fly.** Not open in the sense of undecided — Supabase ships in v1 ([README](README.md#sixth-change-supabase-over-neon)). Recorded here because Phase 15 owns the migration if the trigger fires: per-run storage materially above ~0.4 MB (measured 2026-08-10; the trigger is not currently met), or an unreliable keepalive. The cost of moving is auth, not data — Supabase Auth is providing Google and GitHub OAuth for free, and self-hosting Postgres means rebuilding that in [Phase 12](phase-12-api-auth-quotas.md).
2. **Custom domain?** ~$10–15/year, and it materially improves how the artifact reads to a recruiter versus a `*.vercel.app` URL. Recommend yes — it is the cheapest credibility available in the whole plan.
3. **Public run history?** Showing recent anonymised queries would demonstrate real usage. Adds a privacy surface and a moderation obligation. Recommend deferring; benchmark reports already carry the demo.
