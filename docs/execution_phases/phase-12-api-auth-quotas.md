# Phase 12 — API, Auth, Quotas & Guardrails

| | |
|---|---|
| **Depends on** | [11](phase-11-synthesis-report-assembly.md) |
| **Unlocks** | [13](phase-13-frontend.md), [15](phase-15-deployment-observability.md) |
| **Milestone** | No |
| **Concrete output** | An authenticated HTTP API: OAuth login, quota-checked run creation, SSE event stream, public benchmark reports, and a kill switch that degrades to read-only |

---

## Objective

Put the pipeline behind HTTP with the access control and spend guardrails that make it safe to expose publicly.

## What Supabase changes

The masterplan (§8.1) specifies FastAPI + Authlib, an httponly `SameSite=Lax` session cookie, and hand-rolled `users` / `identities` tables keyed so that the same person arriving via Google and later GitHub with one email lands on one account.

Supabase Auth does all of that natively. Its `auth.users` / `auth.identities` schema *is* the masterplan's design, including cross-provider linking by email. So this phase implements **token verification**, not an OAuth flow — the browser talks to Supabase for login, and the API verifies the resulting JWT.

That removes the callback dance, PKCE handling, token refresh, and identity-merge logic. What remains is the interesting part: quotas, concurrency, and guardrails.

---

## Scope

### In

- FastAPI application, routing, error handling
- Supabase JWT verification and request-scoped user identity
- `POST /runs` with quota enforcement
- SSE streaming with reconnect support
- Public read access to benchmark reports
- Quota, concurrency queue, kill switch, Turnstile, input validation
- JSON export and permalinks

### Out

- The UI ([Phase 13](phase-13-frontend.md))
- Deployment ([Phase 15](phase-15-deployment-observability.md))
- Quota *values* — every knob stays `TBD` until [Phase 14](phase-14-benchmark-calibration.md) measures real numbers. This phase builds the mechanism; the numbers arrive later.

---

## Deliverables

```
src/api/web/
├── __init__.py
├── app.py             # FastAPI app, middleware, error handlers
├── auth.py            # Supabase JWT verification, current_user
├── quota.py           # per-user, global, concurrency
├── killswitch.py
├── turnstile.py
├── sse.py             # event stream + replay
└── routes/
    ├── runs.py
    ├── reports.py
    └── health.py
tests/integration/test_api.py
tests/integration/test_quota.py
tests/integration/test_sse.py
```

---

## Design

### Auth

Login happens in the browser against Supabase. The API receives a JWT in `Authorization: Bearer`, verifies it against Supabase's JWKS (cached, refreshed on `kid` miss), and resolves `sub` to a `user_profiles` row, creating one on first sight.

```python
async def current_user(token: str = Depends(bearer)) -> User: ...
async def optional_user(...) -> User | None: ...   # public endpoints
```

**Verification is local** — JWKS signature check, not a network call per request. Expiry, issuer and audience are all checked; a test asserts each rejection independently, because "we verify the token" is the kind of claim that is easy to get subtly wrong and never notice.

Access model, per masterplan §8.1:

| | Logged out | Logged in |
|---|---|---|
| Benchmark reports (incl. drill-down) | ✅ full | ✅ full |
| Live runs | ❌ | ✅ subject to quota |

**No bring-your-own-key**, per masterplan §12.8: the unit economics do not require it, and accepting third-party secrets on a public site with no security review is liability with zero upside. The escape hatch for a power user is self-hosting — the repo is open, keys go in `.env`. Worth stating in the README rather than leaving as an unexplained absence.

### Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/runs` | required | Turnstile + quota + concurrency checked before any spend |
| `GET` | `/runs/{id}` | owner or public | Status and report |
| `GET` | `/runs/{id}/events` | owner or public | SSE, `Last-Event-ID` supported |
| `GET` | `/runs/{id}/report.json` | owner or public | Export |
| `GET` | `/runs/{id}/claims/{claim_id}` | owner or public | Drill-down: quote, span, context, source |
| `GET` | `/reports/benchmark` | none | The public homepage set |
| `GET` | `/health` | none | Liveness + kill-switch state |

`POST /runs` returns immediately with a run id; work happens in the executor. If disambiguation is needed ([Phase 09](phase-09-interpreter-planner.md)), the response carries chips and the run waits for a `PATCH` with the resolved brief — the ordinary HTTP round trip the masterplan describes (§12.1), which is precisely why no in-graph interrupt mechanism is needed.

The drill-down endpoint returns `quote`, `char_start`, `char_end`, `quote_context`, `context_offset`, and full source text when still cached — so [Phase 13](phase-13-frontend.md) can highlight the span whether or not the source survived eviction ([Phase 03](phase-03-fetch-source-cache.md)).

### SSE

Event types per masterplan §4.10: `plan.created`, `task.started`, `task.completed`, `task.failed`, `finding.added`, `report.ready`.

> The frontend renders the plan as a live checklist. People tolerate two minutes when they can see what is happening. They abandon a spinner at twenty seconds.

Three implementation requirements that make that true in practice:

- **Replay on reconnect.** Events are persisted ([Phase 02](phase-02-executor-core.md)); `Last-Event-ID` resumes from a cursor. A dropped connection mid-run must not lose the run — mobile connections drop routinely and a two-minute window is a long time to stay connected.
- **Heartbeats.** A comment frame every 15s keeps proxies from closing an idle stream.
- **Terminal close.** The stream closes after `report.ready` or a terminal failure, so clients do not hold connections open indefinitely.

### Quotas and guardrails

Masterplan §8.3's layered defence, each layer independent:

| Layer | Mechanism |
|---|---|
| Public surface | Benchmark reports served from cache, instantly, zero cost. **Most visitors never trigger a live run.** |
| Bot filter | Cloudflare Turnstile before a live run |
| Quota | Per user per day, plus a global daily cap |
| Concurrency | K concurrent runs, queue with visible position |
| Kill switch | On daily cap exhaustion, serve reports only and say live runs resume tomorrow |
| Input validation | 300-char cap, reject non-product queries, reject injection attempts, blocklist harmful categories |
| Budget cap | Per-run spend and fan-out ceiling ([Phase 02](phase-02-executor-core.md)) |

**The concurrency queue matters more than it looks.** Without it, a burst of ten simultaneous runs can drain the daily search allowance in sixty seconds. K concurrent runs with a visible queue position converts a spend spike into a wait — and a visible position is a much better experience than an opaque rejection.

Quota checks are **atomic**, in one transaction with the run insert. A read-then-write check races under concurrent requests, which is exactly the condition a quota exists for:

```sql
insert into runs (...) select ...
 where (select count(*) from runs
         where user_id = $1 and started_at > now() - interval '1 day') < $quota;
-- 0 rows inserted -> quota exceeded
```

`user_profiles.quota_override` allows per-user exceptions (the admin, a demo account) without a code change.

**Kill switch** is a database flag, not a deploy. When the global daily cap is hit it flips automatically; it can also be flipped manually. `GET /health` reports its state, and `POST /runs` returns a clear, honest message — live runs resume tomorrow — rather than a generic 429.

### Input validation

Runs before anything else on `POST /runs`, reusing [Phase 09](phase-09-interpreter-planner.md)'s validator so the rules exist in one place. Each rejection class returns a distinct, actionable message: a 301-character query and a blocklisted category are different problems and deserve different responses.

### Error handling

Every error returns a typed JSON body with a stable `code`. No stack traces, no internal identifiers, no vendor error text passed through — a vendor message can leak an API key fragment or an internal URL. Errors are logged with the run id and a correlation id so a user-reported failure is traceable from a code alone.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Integration | Valid JWT → authenticated; expired → 401; bad signature → 401; wrong issuer → 401; wrong audience → 401 | Each rejection independently — the easy-to-get-wrong part |
| Integration | JWKS cached; `kid` miss triggers exactly one refetch | No per-request network call |
| Integration | First login creates `user_profiles`; second reuses it | Idempotent provisioning |
| Integration | Logged out: benchmark reports readable **including drill-down**; `POST /runs` 401 | The masterplan access model |
| Integration | **Quota atomicity** — N concurrent requests at limit N−1 admit exactly N−1 | The race condition that matters |
| Integration | `quota_override` respected | Per-user exceptions |
| Integration | Global cap trips kill switch; reports still served; `POST /runs` returns the honest message | Graceful degradation |
| Integration | Concurrency limit queues rather than rejects; position reported and decreases | Burst absorption |
| Integration | Turnstile failure blocks the run **before** any spend | Bot filter placement |
| Integration | Input validation: over-length, non-product, injection, blocklisted — each with its own code | Actionable errors |
| Integration | SSE delivers events in order; closes after `report.ready` | Stream lifecycle |
| Integration | **SSE reconnect with `Last-Event-ID` resumes without loss or duplication** | The mobile case |
| Integration | SSE heartbeat frames present during a quiet period | Proxy survival |
| Integration | Drill-down endpoint returns reconstructable highlight data when source text is evicted | [Phase 03](phase-03-fetch-source-cache.md) mitigation works through the API |
| Integration | Access control: user A cannot read user B's non-public run | Authorisation, not just authentication |
| Integration | Errors never leak stack traces or vendor messages | Information disclosure |

The quota atomicity test is the one worth writing carefully. A naive check-then-insert passes every single-threaded test and fails exactly when it matters.

---

## Exit criteria

- [ ] Supabase JWT verification local, with all five rejection classes tested
- [ ] JWKS cached with `kid`-miss refresh
- [ ] Logged-out users read benchmark reports with full drill-down; cannot run
- [ ] `POST /runs` enforces Turnstile → validation → quota → concurrency, in that order, before any spend
- [ ] Quota enforcement atomic under concurrency
- [ ] Kill switch degrades to read-only with an honest message; state visible on `/health`
- [ ] Concurrency queue reports position
- [ ] SSE streams all six event types, heartbeats, closes on terminal
- [ ] SSE reconnect resumes losslessly via `Last-Event-ID`
- [ ] Drill-down endpoint works with and without cached source text
- [ ] Cross-user authorisation enforced
- [ ] Errors typed, stable codes, no leakage
- [ ] No BYO-key path anywhere
- [ ] All quota knobs read from config, still `TBD`, with `None` handled explicitly
- [ ] Coverage ≥ 85% on `src/api/web/`

---

## Risks

| Risk | Mitigation |
|---|---|
| Quota race admits over-limit runs | Atomic conditional insert; concurrency test at the boundary. |
| Supabase Auth outage blocks all logins | Benchmark reports stay readable — they need no auth. Degraded, not down. The masterplan's public-surface-first design already covers this. |
| SSE breaks behind a proxy that buffers | Heartbeats plus `X-Accel-Buffering: no`. Verified against the real deployment in [Phase 15](phase-15-deployment-observability.md), since proxy behaviour is environment-specific. |
| Turnstile blocks legitimate users | Only gates live runs, never reading. A blocked user still gets the full public experience. |
| Kill switch trips too eagerly and the demo is dead | Threshold derived in [Phase 14](phase-14-benchmark-calibration.md), plus manual override. Benchmark reports — the recruiter-facing surface — are unaffected either way. |
| JWT verification subtly wrong | Five independent rejection tests. Verification is local and standard; the risk is in what is *not* checked, so each check is asserted separately. |

## Open decisions

1. **Anonymous trial run?** Masterplan §12.8 rejects anonymous runs because search credits are the scarce resource and anonymous runs cannot be quota'd meaningfully. Turnstile plus IP-based limiting could allow exactly one, improving conversion. Weigh against abuse risk after [Phase 14](phase-14-benchmark-calibration.md) fixes the real cost per run — if a run is genuinely ~$0.04, one anonymous run is a cheap acquisition cost.
2. **Queue position transport.** Poll `GET /runs/{id}` or push over SSE before the run starts? SSE is nicer but means holding connections for queued runs, which multiplies idle connections during exactly the burst the queue exists to absorb. Lean polling until the run starts, then SSE.
