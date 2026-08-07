# AGENTS.md

AI Product Investigator: type a product idea, get an evidence-backed discovery report where every sentence cites a verbatim span in a fetched page. Full spec: `ai-product-investigator-masterplan.md`; 16-phase plan in `docs/execution_phases/`.

## Start here
- `docs/tracker.md` — living status (current phase, open decisions, next steps). Read first when resuming.
- `docs/working_knowledge.md` — architecture + conventions + known gotchas. Durable reference.
- `docs/external_apis.md` — measured vendor limits/costs (Exa, OpenRouter/DeepSeek, GitHub, ...).
- Masterplan is the authority on *why*; `docs/execution_phases/` phase docs are the *how*.

## Toolchain
- Python 3.12 managed by **uv** (`uv sync --extra dev`). Ruff (lint+format, line-length 100) + `mypy --strict` on `src/api/`.
- Package root `src/api/` (editable). One module per concern, no `utils.py`.
- DB: Postgres 16 + pgvector via `docker-compose.yml`. Runtime access is **asyncpg only**; psycopg/SQLAlchemy exist solely to run Alembic (autogenerate off — hand-written migrations in `migrations/versions/`).
- pydantic-settings; secrets have **no defaults** — copy `.env.example` to `.env`.

## Commands
- `make check` — ruff lint + format check + mypy --strict + pytest (== CI gate).
- `make unit` (no Postgres) / `make integration` (needs Postgres + `ai_pi_test` DB).
- Single test: `uv run pytest tests/unit/test_span.py` (or `tests/integration/...`).
- Setup: `cp .env.example .env` → `make db-up` → create `ai_pi_test` once (`docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE ai_pi_test;"`) → `make migrate`.
- Migrate the *test* DB explicitly (default targets `ai_pi`): `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_pi_test uv run alembic upgrade head`.
- End-to-end live run: `python -m api.cli run "<idea>"`; `python -m api.cli inspect|replay <run_id>`.
- Concurrency suite (races are probabilistic): `uv run pytest tests/integration/test_lease.py tests/integration/test_executor.py tests/integration/test_chaos.py --count=50 --no-cov`.

## Test quirks
- `@pytest.mark.live` (tests/live/) excluded by default; default run enforces `--cov-fail-under=85`.
- Integration tests **skip gracefully** (not fail) when Postgres is unreachable — green `make check` without a live Postgres still "passes". CI runs `make migrate` against `TEST_DATABASE_URL` first.
- Tests hitting shared persistent tables (`search_cache`, `search_credit_usage`, `llm_response_cache`, `extraction_cache`) need `uuid4`-suffixed keys or they pass once then fail on re-run against the long-lived local Postgres.
- Never run `test_migrations.py`'s upgrade/downgrade cycle concurrently with any other Postgres-backed test against the same DB.

## Non-obvious conventions
- **Never `ruff format .` at repo root** — it rewrites Python fences inside markdown docs. Always scope to `src tests migrations`.
- `TID251` bans imports of `spikes` and `api.llm.client` outside `api.llm`; only `gateway.structured()` / `embed.py` may call the client — new model code must go through `api.llm.gateway`.
- Prompts are versioned `*.md` under `src/api/prompts/` (never inline). A per-call-varying placeholder must live in the `## user` section (after `cache_prefix_ends_after`) or it is never substituted and silently breaks repair/cache logic.
- Core guarantee: claims are only written if their quote is found verbatim (`bind_span`: `str.find`, no fuzzy matching; ambiguous quotes are dropped). Span offsets are Python code-point indices.
- `extractor_version = {prompt_version}-{model}` — a prompt edit or model swap must invalidate caches.
- Integration HTTP tests use scripted `httpx.MockTransport` (`tests/integration/_http.py`) for own-layer mechanics; VCR cassettes (`tests/fixtures/cassettes/`) replay only real vendor traffic.
