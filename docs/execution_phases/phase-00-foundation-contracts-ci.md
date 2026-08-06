# Phase 00 — Foundation, Contracts & CI

| | |
|---|---|
| **Depends on** | — |
| **Unlocks** | Every other phase |
| **Milestone** | No |
| **Concrete output** | `make check` green on a clean clone; migrations apply and roll back; every typed contract from the masterplan exists as a Pydantic model with round-trip tests |

---

## Objective

Freeze the shapes that every later phase depends on — database schema, claim vocabulary, entity keys, plan DAG, event types, report contract — and stand up the tooling that enforces them. Nothing in this phase does research work. Everything in this phase is load-bearing for the eleven phases that follow.

## Why this phase is first

The masterplan's whole design rests on typed, closed vocabularies: contradiction detection is a `GROUP BY` only because attributes are enumerable (§4.7); the drill-down promise is structural only because `findings.claim_ids` is non-null (§4.3). If those types get invented incrementally per-phase, they drift, and the guarantees quietly stop holding.

Getting the contracts wrong here is cheap to fix. Getting them wrong in Phase 10 means rewriting six modules.

---

## Scope

### In

- Repo scaffold, `pyproject.toml`, dependency pinning, `Makefile`
- Docker Compose for local Postgres 16 + pgvector
- Alembic migrations covering the full masterplan §4.3 schema
- Every shared Pydantic contract
- Config loading (`pydantic-settings`), `.env.example`
- Structured logging + OpenTelemetry tracer bootstrap
- pytest scaffold with the three-tier layout, Postgres fixture, coverage gate
- GitHub Actions CI

### Out

- Any business logic. No fetching, no LLM calls, no planning.
- Supabase project provisioning (that is [Phase 15](phase-15-deployment-observability.md); local Postgres is sufficient here and the schema is identical).
- Auth tables — Supabase Auth owns `auth.users` / `auth.identities`. See the schema note below.

---

## Deliverables

```
pyproject.toml                     # deps, ruff, mypy, pytest config
Makefile                           # check, test, fmt, migrate, db-up, db-reset
docker-compose.yml                 # postgres:16 + pgvector
.env.example
alembic.ini
migrations/versions/0001_initial.py
src/api/
├── __init__.py
├── config.py                      # Settings
├── db.py                          # asyncpg pool, session helper
├── logging.py                     # structlog + OTel bootstrap
├── models/
│   ├── brief.py                   # ResearchBrief
│   ├── claims.py                  # ClaimAttribute, Claim, Grade
│   ├── entity.py                  # EntityKey, Maturity
│   ├── plan.py                    # TaskKind, PlanNode, Plan
│   ├── events.py                  # RunEvent union
│   └── report.py                  # Report (output contract)
└── prompts/                       # empty; versioned prompt files land here
tests/
├── conftest.py                    # pg fixture, cassette config
└── unit/test_contracts.py
.github/workflows/ci.yml
```

---

## Design

### Database schema

Implements masterplan §4.3 with three deliberate changes.

**Change 1 — auth tables are Supabase's.** The masterplan defines `users` and `identities`. Supabase Auth already provides `auth.users` and `auth.identities` with the exact semantics required (one account per email, multiple provider identities linked). Do not duplicate them. Instead:

```sql
-- Our own per-user state, keyed to Supabase's auth.users
create table user_profiles (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  quota_override int,
  is_admin      bool not null default false,
  created_at    timestamptz not null default now()
);
```

Locally (no Supabase), a migration guard creates a minimal `auth.users` stub so the FK resolves and tests run identically. The stub is skipped when the `auth` schema already exists.

**Change 2 — quote context window on `claims`.** Supabase free tier is 500 MB and `sources.extracted_text` dominates it. To let drill-down survive TTL eviction of source text, each claim carries its own excerpt:

```sql
claims (
  ...,
  quote          text not null,
  char_start     int not null,
  char_end       int not null,
  quote_context  text not null,   -- ±2000 chars around the span, frozen at extraction
  context_offset int not null,    -- char_start - start of quote_context
  ...
)
```

The UI highlights `quote` inside `quote_context` using `context_offset`, so a report from three months ago still drills down after its source row was evicted. Full source text remains available while cached, and the UI prefers it when present.

**Change 3 — `sources.is_pinned`.** Benchmark runs must never lose drill-down. `is_pinned = true` exempts a source row from TTL eviction.

Full schema (see the migration for exact DDL):

```sql
user_profiles(user_id, quota_override, is_admin, created_at)

runs(id, user_id, query, brief jsonb, status, cost_usd, coverage numeric,
     is_benchmark bool, is_public bool, started_at, finished_at)

tasks(id, run_id, kind, args jsonb, status, priority, attempts,
      lease_token uuid, lease_expires_at, cost_usd, latency_ms, error)

sources(id, canonical_url unique, root_key, fetched_at, http_status,
        content_hash, extracted_text, retrieval_reason, ttl_expires_at,
        is_pinned bool not null default false)

entities(id, entity_key unique, display_name, maturity, meta jsonb)
entity_aliases(entity_id, alias_key unique)

claims(id, run_id, entity_id, attribute, value_text, value_num, unit, as_of,
       source_id, quote, char_start, char_end, quote_context, context_offset,
       grade, extractor_version, confidence numeric, superseded_by,
       unique(run_id, source_id, attribute, char_start))

findings(id, run_id, kind, statement, claim_ids int[], support_count, confidence)
reports(id, run_id, payload jsonb)
```

**Indexes that matter** (add now, not after the first slow query):

```sql
create index on tasks (run_id, status, priority);
create index on tasks (status, lease_expires_at) where status = 'running';
create index on claims (run_id, entity_id, attribute) where superseded_by is null;
create index on sources (ttl_expires_at) where is_pinned = false;
create index on entity_aliases (alias_key);
```

The partial index on `tasks (status, lease_expires_at)` is what makes the [Phase 02](phase-02-executor-core.md) crash-recovery sweep cheap.

**Constraints as guarantees.** Two CHECK constraints encode masterplan rules at the database level rather than trusting application code:

```sql
alter table findings add constraint findings_must_cite
  check (array_length(claim_ids, 1) >= 1);

alter table claims add constraint claims_span_valid
  check (char_end > char_start and char_start >= 0);
```

Rule 1 of the masterplan ("every prose sentence carries at least one `claim_id`") is now impossible to violate by accident. A finding with no citations cannot be inserted.

### Typed contracts

**Claim vocabulary** (masterplan §4.4) as a closed enum. This is the single most important type in the system — contradiction detection, cross-entity comparison, and injection resistance all derive from it being closed.

```python
class ClaimAttribute(StrEnum):
    PRICING_MODEL           = "pricing.model"
    PRICING_ENTRY_USD_MONTH = "pricing.entry_usd_month"
    PRICING_FREE_TIER       = "pricing.free_tier"
    PRICING_TRIAL_DAYS      = "pricing.trial_days"
    PRODUCT_LAUNCH_DATE     = "product.launch_date"
    PRODUCT_PLATFORMS       = "product.platforms"
    PRODUCT_INTEGRATIONS    = "product.integrations"
    COMPANY_FUNDING_TOTAL   = "company.funding_total_usd"
    COMPANY_STAGE           = "company.stage"
    OSS_REPO                = "oss.repo"
    OSS_STARS               = "oss.stars"
    OSS_STARS_90D_DELTA     = "oss.stars_90d_delta"
    OSS_LAST_COMMIT_AT      = "oss.last_commit_at"
    OSS_LICENSE             = "oss.license"
    OSS_CONTRIBUTORS_90D    = "oss.contributors_90d"
    # Parameterised families validated by regex, not enumerated:
    #   feature.<slug>.present
    #   complaint.<theme>
    #   request.<theme>  |  request.<theme>.reactions
```

Parameterised families (`feature.*`, `complaint.*`, `request.*`) take an open slug but a **closed shape**: `^(feature\.[a-z0-9-]{2,40}\.present|complaint\.[a-z0-9-]{2,40}|request\.[a-z0-9-]{2,40}(\.reactions)?)$`. A validator enforces this, so a model cannot invent `pricing.something_new` but can legitimately report `complaint.receipt-ocr-accuracy`.

Each attribute declares its value type so downstream code never guesses:

```python
ATTRIBUTE_SPEC: dict[str, AttributeSpec] = {
    "pricing.entry_usd_month": AttributeSpec(kind=NUMERIC, unit="usd/month"),
    "pricing.free_tier":       AttributeSpec(kind=BOOLEAN),
    "pricing.model":           AttributeSpec(kind=ENUM,
                                             choices={"seat","usage","flat","freemium"}),
    ...
}
```

`Claim` validates `value_num` vs `value_text` against this spec on construction. A numeric attribute with only `value_text` set is a validation error, not a silent downstream `None`.

**Entity key** (masterplan §4.5) as a parsed value object, not a string:

```python
class EntityScheme(StrEnum):
    WEB = "web"; GH = "gh"; NPM = "npm"; PYPI = "pypi"
    CHROME = "chrome"; IOS = "ios"; HF = "hf"; PH = "ph"

@dataclass(frozen=True)
class EntityKey:
    scheme: EntityScheme
    value: str
    def __str__(self) -> str: return f"{self.scheme}:{self.value}"
    @classmethod
    def parse(cls, raw: str) -> EntityKey: ...
```

Derivation logic (PSL handling, `include_psl_private_domains=True`) belongs to [Phase 07](phase-07-entity-resolution.md). This phase only fixes the type and its parse/format round-trip.

**Plan DAG** (masterplan §4.1). `TaskKind` is a closed enum with per-kind arg models and declared `cost_weight`. `Plan` validates on construction that the graph is acyclic, that every edge references a declared node, and that `total_budget_weight` equals the sum of node weights.

**Events** (masterplan §4.10). A discriminated union over `plan.created`, `task.started`, `task.completed`, `task.failed`, `finding.added`, `report.ready`. Serialising to SSE wire format is [Phase 12](phase-12-api-auth-quotas.md); the types are frozen here so the executor ([Phase 02](phase-02-executor-core.md)) can emit them before an HTTP layer exists.

**Report** — the masterplan §2 output contract, verbatim, as nested Pydantic models. This is the acceptance target for [Phase 11](phase-11-synthesis-report-assembly.md).

### Config

`pydantic-settings`, loaded from environment, fails fast on missing required values. Secrets are never defaulted — a missing `OPENROUTER_API_KEY` raises at import, not at first LLM call three minutes into a run.

All quota knobs from masterplan §8.2 appear as `None`-defaulted optional fields with a comment pointing at [Phase 14](phase-14-benchmark-calibration.md). They are deliberately unset; code that reads them must handle `None` explicitly.

### Tooling

| Tool | Setting |
|---|---|
| `ruff` | lint + format, replaces black/isort/flake8 |
| `mypy` | `--strict` on `src/api/`, tests excluded |
| `pytest` | `--cov=src/api --cov-fail-under=85`, `-m "not live"` by default |
| `alembic` | autogenerate off; migrations hand-written and reviewed |
| `hypothesis` | available for property tests from Phase 03 onward |

`make check` = `ruff check && ruff format --check && mypy && pytest`. CI runs exactly this — no CI-only steps, so a green local run means a green CI run.

---

## Testing

| Kind | What |
|---|---|
| Unit | Every Pydantic contract round-trips: `Model.model_validate(m.model_dump()) == m`. |
| Unit | `ClaimAttribute` rejects invented attributes; accepts every parameterised family shape; rejects malformed slugs (uppercase, too long, wrong separator). |
| Unit | `Claim` value-type validation: numeric attribute without `value_num` fails; boolean with `value_text="yes"` fails; enum outside `choices` fails. |
| Unit | `EntityKey.parse(str(k)) == k` for all schemes — **property test** over generated keys. |
| Unit | `Plan` rejects: cycles, edges to undeclared nodes, budget mismatch, duplicate node ids. |
| Unit | `Report` parses the exact JSON literal from masterplan §2 without modification. This is a regression test against contract drift. |
| Integration | Migration applies to empty Postgres; `downgrade` then `upgrade` is idempotent; final schema matches models. |
| Integration | `findings_must_cite` rejects an empty `claim_ids`; `claims_span_valid` rejects `char_end <= char_start`. Constraints are tested, not assumed. |
| Integration | `claims` unique constraint rejects a duplicate `(run_id, source_id, attribute, char_start)`, and `ON CONFLICT DO NOTHING` swallows it. This is the [Phase 02](phase-02-executor-core.md) idempotency guard — proven here. |
| Meta | `make check` on a clean clone with no network beyond package install. |

The masterplan §2 JSON literal being a test fixture matters more than it looks: it makes the output contract executable. If someone later renames `entry_usd_month`, a test fails in Phase 00 rather than a frontend breaking in Phase 13.

---

## Exit criteria

- [ ] `git clone && make db-up && make check` passes on a clean machine
- [ ] `alembic upgrade head` then `alembic downgrade base` then `upgrade head` succeeds
- [ ] All contract models exist with round-trip tests; `mypy --strict` clean
- [ ] Masterplan §2 example JSON parses into `Report` unmodified
- [ ] Both CHECK constraints and the `claims` unique constraint have passing integration tests
- [ ] CI runs `make check` on push and PR, green
- [ ] Coverage ≥ 85% on `src/api/models/`
- [ ] `.env.example` lists every setting; app raises a clear error on each missing required one
- [ ] A trace with at least one span is emitted to the console exporter on startup

---

## Risks

| Risk | Mitigation |
|---|---|
| Contracts churn in later phases anyway | Accepted for *additive* change; any *breaking* change to a Phase 00 contract requires a note in `docs/tracker.md` explaining what was learned. Churn is a signal the masterplan was underspecified there. |
| Supabase `auth` schema differs from the local stub | Phase 15 runs the full migration against a real Supabase project before deploy. The stub is FK-shaped only; nothing reads its columns. |
| 500 MB ceiling reached sooner than modelled | `quote_context` denormalisation and TTL eviction are in from day one, not retrofitted. Phase 14 measures actual bytes per run and re-derives the ceiling. |
| pgvector unused until much later | Extension enabled in the initial migration anyway — enabling it later on Supabase is a dashboard action that is easy to forget at deploy time. |

## Open decisions

1. **asyncpg directly vs SQLAlchemy Core.** Leaning asyncpg + hand-written SQL: the executor's `FOR UPDATE SKIP LOCKED` and the contradiction `GROUP BY` are both clearer as raw SQL, and they are the two most interesting queries in the project. Decide before writing `db.py`; changing later touches every module.
2. **`extractor_version` format.** Proposal: `{prompt_file_hash[:8]}-{model_id}`, so a prompt edit or model swap invalidates the extraction cache automatically. Confirm in [Phase 06](phase-06-claim-extraction-span-binding.md).
