# Phase 02 — Executor Core

| | |
|---|---|
| **Depends on** | [00](phase-00-foundation-contracts-ci.md) |
| **Unlocks** | [10](phase-10-task-handlers-e2e.md) |
| **Milestone** | No |
| **Concrete output** | A generic DAG executor that survives a chaos suite — killed workers, expired leases, duplicate writes, budget exhaustion — using synthetic tasks only. Zero external dependencies. |

---

## Objective

Build the concurrent task executor from masterplan §4.2 as a **domain-agnostic** component: it runs a DAG of tasks over a Postgres table with leasing, retries, budget enforcement and crash recovery, and it knows nothing about products, claims, or the web.

## Why this phase is early, and standalone

The masterplan calls this "the interesting engineering in this project" (§12.1) and explicitly rejects LangGraph to keep it. It is also the piece most likely to have subtle concurrency bugs — and those bugs are almost impossible to diagnose once real network I/O, LLM latency and partial failures are layered on top.

So it is built and hardened **against synthetic tasks**: `sleep_task`, `fail_n_times_task`, `hang_forever_task`, `emit_events_task`. Every failure mode is deterministic and reproducible in milliseconds. Real task handlers arrive in [Phase 10](phase-10-task-handlers-e2e.md) and plug into an interface that has already been proven correct.

This is also why the executor sits behind `Executor.submit(plan) -> AsyncIterator[Event]` (masterplan §4.2) — a boundary narrow enough to swap, and narrow enough to test exhaustively.

---

## Scope

### In

- `asyncio.TaskGroup` dispatch loop with per-service semaphores
- Postgres-backed leasing via `SELECT … FOR UPDATE SKIP LOCKED`
- Lease expiry, crash-recovery sweep, and the two idempotency guards
- Retry with jittered exponential backoff, restricted to retryable failures
- Budget-weight enforcement and skip accounting
- Per-task timeout
- Event emission as an async iterator
- Partial-failure semantics: a dead branch reduces coverage, it does not fail the run

### Out

- Any real task implementation ([Phase 10](phase-10-task-handlers-e2e.md))
- The planner that produces DAGs ([Phase 09](phase-09-interpreter-planner.md)) — tests construct `Plan` objects directly
- SSE wire format ([Phase 12](phase-12-api-auth-quotas.md)) — events are Python objects here
- **Redis, arq, Celery.** See [D3](README.md#deviations-from-the-masterplan): arq requires Redis, contradicting the masterplan's own stated goal. The Postgres task table *is* the queue.

---

## Deliverables

```
src/api/executor/
├── __init__.py
├── core.py            # Executor: dispatch loop, TaskGroup, semaphores
├── lease.py           # claim, renew, release, sweep_expired
├── budget.py          # BudgetTracker
├── retry.py           # backoff policy, retryable classification
└── protocol.py        # TaskHandler protocol, HandlerRegistry
tests/
├── unit/test_budget.py
├── unit/test_retry.py
├── integration/test_lease.py
├── integration/test_executor.py
└── integration/test_chaos.py     # the interesting one
```

---

## Design

### Handler protocol

The seam between generic execution and domain work:

```python
class TaskHandler(Protocol):
    kind: TaskKind
    cost_weight: int
    service: str            # semaphore bucket: "search" | "crawl" | "llm" | "none"
    timeout_s: float

    async def run(self, ctx: TaskContext, args: BaseModel) -> HandlerResult: ...
```

`TaskContext` carries `run_id`, `task_id`, `lease_token`, a `RetrievalBudget` handle, and an `emit(event)` callback. Handlers never touch the database directly for task state — they return a `HandlerResult` and the executor persists it under the lease guard. This is what makes the idempotency guarantee enforceable rather than a convention each handler must remember.

`HandlerResult` carries produced artefacts (claims, discovered entities), spawned follow-up tasks, and measured cost. Handlers may **enqueue new tasks** — this is how `discover_competitors` fans out into N `profile_product` tasks. The DAG is therefore dynamic, and the budget check is what stops runaway fan-out.

### Leasing

Masterplan §4.2 verbatim, in one transaction:

```sql
begin;
  select id from tasks
   where status = 'pending' and run_id = $1
   order by priority
   limit 1
   for update skip locked;

  update tasks
     set status = 'running',
         lease_token = gen_random_uuid(),
         lease_expires_at = now() + interval '2 minutes',
         attempts = attempts + 1
   where id = $2;
commit;
```

`SKIP LOCKED` is the whole trick — a second worker takes the next row instead of blocking on the one the first just locked.

**Lease renewal.** Two minutes is too short for a slow crawl of a big page. Long-running handlers get a heartbeat that extends `lease_expires_at` while the handler is demonstrably alive. Renewal is conditional on still holding the token:

```sql
update tasks set lease_expires_at = now() + interval '2 minutes'
 where id = $1 and lease_token = $2;
```

Zero rows affected means the lease was lost — the handler is cancelled immediately rather than continuing work that will be discarded.

**Crash recovery**, one sweep on worker startup and periodically thereafter:

```sql
update tasks
   set status = 'pending', lease_token = null, lease_expires_at = null
 where status = 'running' and lease_expires_at < now();
```

Backed by the partial index from [Phase 00](phase-00-foundation-contracts-ci.md), so it stays cheap as `tasks` grows.

### Idempotency — the non-negotiable part

The leasing scheme makes idempotency mandatory, not optional. A worker that hangs past its lease will be superseded by another worker, then wake up and try to write anyway. Two independent guards, both from masterplan §4.2:

**Guard 1 — completion is rejected if the lease was lost:**

```sql
update tasks set status = 'done', cost_usd = $3, latency_ms = $4
 where id = $1 and lease_token = $2;
-- 0 rows affected → discard the work entirely, write no claims
```

**Guard 2 — belt and braces at the data layer:**

```sql
-- unique (run_id, source_id, attribute, char_start), enforced in Phase 00
insert into claims (...) values (...) on conflict do nothing;
```

Guard 1 is the primary mechanism; Guard 2 catches the race where a superseded worker wins the interleaving. Both are tested independently in the chaos suite — a passing test for one must not mask a broken other.

### Concurrency limits

One semaphore per external service, per masterplan §4.2: search 4, crawl 8, LLM 6. Values are config, not constants, because [Phase 14](phase-14-benchmark-calibration.md) tunes them against measured vendor rate limits from [Phase 01](phase-01-dependency-validation-spike.md).

Semaphores are per-worker-process. With multiple workers, effective concurrency multiplies — the global ceiling is enforced by the vendor's own rate limit and the run-level budget, not by the semaphore. Documented explicitly so nobody later mistakes it for a distributed limiter.

### Budget enforcement

Masterplan §4.2's counter, made concrete:

```python
spent = 0
async with asyncio.TaskGroup() as tg:
    for task in pending(run_id):
        if spent + task.cost_weight > run_budget:
            mark_skipped(task, reason="budget")
            continue
        tg.create_task(execute(task))
        spent += task.cost_weight
```

Two clarifications the masterplan leaves implicit:

- **Budget is charged at dispatch, not completion.** Otherwise a burst of concurrent dispatches all pass the check before any completes, and the cap is meaningless under exactly the concurrency it exists to bound.
- **Skipped ≠ failed.** A budget-skipped task is a distinct terminal status that reduces `coverage` and is surfaced in `coverage.failed_branches`. It must never look like an error to the user.

There is a second, independent cap on **dollars** (`RUN_BUDGET_USD`), checked against accumulated `cost_usd`. Weight bounds fan-out; dollars bound spend. The masterplan is explicit that both exist to protect against *your own bugs* — a retry storm or a fan-out to 200 profile tasks — not against token cost.

### Retry policy

Three attempts, jittered exponential backoff, **only** on 429, 5xx and timeouts (masterplan §4.2). A 404 or a schema-validation failure is not retried — retrying deterministic failures is how a retry storm starts.

Backoff is `min(base * 2**attempt, cap)` with full jitter. Full jitter, not equal jitter: with several workers hitting the same rate-limited vendor, correlated retries are the failure mode being avoided.

Retry budget is per-task. A task that exhausts its attempts is marked `failed` with its error recorded, and the run continues — **partial failure is the normal case** (masterplan §4.2).

### Events

The executor emits typed events ([Phase 00](phase-00-foundation-contracts-ci.md) contracts) through an async iterator. Because a run may be observed by an HTTP client that connects late or reconnects, events are also persisted, and the iterator can replay from a cursor. This is cheap now and impossible to retrofit once [Phase 13](phase-13-frontend.md) depends on the stream.

---

## Testing

This is the phase where test quality decides whether the system is debuggable in production. Synthetic handlers make every scenario deterministic.

### Synthetic handlers

```python
sleep_task(ms)            # controllable duration
fail_n_times_task(n)      # fails n times then succeeds — retry testing
always_fail_task(code)    # deterministic failure with a chosen error class
hang_forever_task()       # never returns — lease expiry testing
spawn_task(n)             # enqueues n children — fan-out and budget testing
emit_events_task(k)       # emits k events — stream testing
```

### Unit

| Test | Asserts |
|---|---|
| Budget arithmetic | Dispatch stops exactly at the cap; skipped tasks counted; charge-at-dispatch semantics hold under simulated concurrency |
| Retry classification | 429/500/502/503/504/timeout retryable; 400/401/403/404/422 not |
| Backoff | Monotonic growth, respects cap, jitter within bounds — **property test** over attempt numbers |
| Lease-expiry arithmetic | Clock-skew edge cases around the boundary |

### Integration (real Postgres)

| Test | Asserts |
|---|---|
| Single worker, linear DAG | Tasks execute in dependency order; all reach `done` |
| Two workers, wide fan-out | No task executes twice — asserted by a handler that increments a counter row |
| `SKIP LOCKED` contention | 8 concurrent claimers over 8 pending tasks each get a distinct task, none blocks |
| Dependency ordering | A task with unmet dependencies is never claimed |
| Dynamic enqueue | `spawn_task` children are picked up in the same run without a restart |
| Event ordering | Per-task events arrive in causal order; replay from cursor yields the same sequence |

### Chaos — the phase's real deliverable

| Scenario | Method | Asserts |
|---|---|---|
| **Worker killed mid-task** | `SIGKILL` a worker holding leases | Sweep re-queues its tasks; another worker completes them; no duplicate side effects |
| **Lease expiry then zombie write** | `hang_forever_task`, force expiry, let a second worker complete, then release the first | Guard 1 rejects the zombie's completion (0 rows); no claims written by the zombie |
| **Guard 2 in isolation** | Disable Guard 1, force a duplicate claim insert | Unique constraint + `ON CONFLICT DO NOTHING` absorbs it; row count unchanged |
| **Retry storm containment** | 50 tasks all `always_fail_task(429)` | Each stops at 3 attempts; total attempts ≤ 150; run completes; backoff observed between attempts |
| **Runaway fan-out** | `spawn_task` recursively spawning children | Budget cap halts it; run terminates; skipped count reported |
| **Postgres restarts mid-run** | Restart the container | Executor reconnects; in-flight tasks recovered via sweep; run completes |
| **All branches fail** | Every task fails | Run reaches a terminal state with `coverage` reflecting total failure — it does **not** hang or crash |
| **Timeout enforcement** | `sleep_task` exceeding `timeout_s` | Task cancelled, marked failed with timeout error, lease released |

The zombie-write test is the one that matters most. It is the scenario the masterplan singles out, it is the hardest to reason about, and it is nearly impossible to reproduce accidentally in real usage — which means without a deliberate test it stays broken silently until it corrupts a report.

### Concurrency verification

Run the integration suite under `pytest-repeat` (≥ 50 iterations) in CI nightly. Concurrency bugs are probabilistic; a single green run is weak evidence. Any flake is a bug to fix, never a test to retry.

---

## Exit criteria

- [ ] Every chaos scenario above passes, repeatably (50× nightly, zero flakes)
- [ ] Two workers against the same run never double-execute a task
- [ ] Zombie-write rejection proven for **both** guards independently
- [ ] Budget cap halts runaway fan-out; skipped tasks distinguishable from failed
- [ ] Retry restricted to retryable classes; storm bounded at attempts × tasks
- [ ] Run with all branches failing terminates cleanly with accurate coverage
- [ ] Executor is importable and usable with zero network access
- [ ] `Executor.submit(plan) -> AsyncIterator[Event]` is the only public entry point
- [ ] No Redis, arq, or Celery anywhere in dependencies
- [ ] Coverage ≥ 90% on `src/api/executor/` (higher bar than default — this is core)

---

## Risks

| Risk | Mitigation |
|---|---|
| Concurrency bug survives to production | Chaos suite is the mitigation, plus repeat-runs nightly. Any flake is treated as a real bug. |
| Lease duration wrong for real handlers | 2 min is a starting guess; heartbeat renewal makes it much less sensitive. [Phase 14](phase-14-benchmark-calibration.md) tunes from measured p95 task latency. |
| Postgres becomes the bottleneck under polling | Claim loop uses a short backoff when the queue is empty rather than tight polling. If it ever matters, `LISTEN/NOTIFY` is the upgrade — no schema change needed. |
| Charge-at-dispatch over-counts when tasks are skipped downstream | Accepted. The cap is bug insurance, not an accountant. Conservative over-counting is the correct failure direction. |
| Synthetic tests do not resemble real workloads | Deliberate trade: this phase proves *mechanism*. [Phase 10](phase-10-task-handlers-e2e.md) proves *behaviour* with real handlers, on top of a mechanism already known-good. |

## Open decisions

1. **Multi-worker in production, or one worker with high concurrency?** The leasing design supports both. Fly ([D4](README.md#deviations-from-the-masterplan)) makes scaling to a second worker machine trivial, so this is a runtime knob rather than an architectural commitment — start with one worker at high asyncio concurrency, scale out if [Phase 14](phase-14-benchmark-calibration.md) shows run latency is worker-bound. The code must not assume either, and the chaos tests keep running multi-worker regardless.
2. **Event retention.** Events persist for replay; how long before pruning? Interacts with the Supabase 500 MB ceiling. Defer to [Phase 14](phase-14-benchmark-calibration.md) once per-run event volume is measured.
