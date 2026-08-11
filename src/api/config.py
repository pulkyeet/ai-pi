from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from the environment.

    Required fields have no default: a missing secret raises at import time,
    not three minutes into a run at first use.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "dev"
    log_level: str = "INFO"
    otel_console_export: bool = True

    database_url: PostgresDsn
    openrouter_api_key: SecretStr
    exa_api_key: SecretStr
    github_token: SecretStr
    producthunt_token: SecretStr | None = None

    # Quota knobs (masterplan §8.2). Deliberately unset until Phase 14
    # measures real per-run search/latency numbers; code that reads them
    # must handle None explicitly.
    runs_per_user_per_day: int | None = None
    global_runs_per_day: int | None = None
    max_concurrent_runs: int | None = None
    run_budget_weight: int | None = None
    run_budget_usd: float | None = None
    run_timeout_s: int | None = None
    max_competitors_profiled: int | None = None
    max_pages_per_entity: int | None = None
    max_community_threads: int | None = None

    # Exa credit allowance guardrails (masterplan §8.2, Phase 04). Both stay
    # unset — and therefore unenforced — until Phase 14 measures real
    # credits-per-run against the $10/mo recurring allowance
    # (docs/external_apis.md). "Daily" and "global daily" collapse to one
    # check: the credit ledger is already system-wide, not per-run/user.
    exa_daily_credit_cap_usd: float | None = None
    exa_global_daily_credit_cap_usd: float | None = None

    # Executor concurrency (masterplan §4.2 — per-service semaphores, one per
    # worker process). Given as concrete numbers in the masterplan itself
    # (unlike the quota knobs above), so they default here rather than
    # waiting on Phase 14; that phase may still retune them against measured
    # vendor rate limits.
    search_concurrency: int = 4
    crawl_concurrency: int = 8
    llm_concurrency: int = 6
    task_lease_duration_s: float = 120.0

    # LLM gateway (masterplan §6, Phase 05). Single model, per masterplan
    # §12.2 — "measure, only route upward if the benchmark shows a real gap".
    # Validated against real traffic in Phase 01 (docs/external_apis.md).
    llm_model: str = "deepseek/deepseek-v4-flash"

    # Langfuse tracing (Phase 05). Unset by default — api.llm.tracing falls
    # back to a no-op tracer so a missing/invalid key never blocks a run,
    # same "None means unconfigured, never a crash" pattern as producthunt_token.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # API, Auth, Quotas & Guardrails (Phase 12). Supabase does OAuth in the
    # browser (masterplan §8.1 minus Authlib — see docs/execution_phases/
    # phase-12-api-auth-quotas.md's "What Supabase changes"); the API only
    # verifies the resulting JWT against Supabase's JWKS. `supabase_url` unset
    # means auth cannot be configured — every real deployment needs it, but
    # local dev without it can still run `make unit`.
    supabase_url: str | None = None
    supabase_jwt_audience: str = "authenticated"

    # Cloudflare Turnstile (masterplan §8.3's bot filter, ahead of any spend).
    # Unset means unconfigured -> no-op, same "None never crashes" pattern as
    # every other optional credential in this file (producthunt_token, etc.);
    # documented explicitly in api.web.turnstile rather than left implicit.
    turnstile_secret_key: SecretStr | None = None

    # Phase 13's frontend origin(s) for CORS. Empty by default (no browser
    # cross-origin caller yet); Phase 13 sets this to its deployed Vercel URL.
    cors_allow_origins: list[str] = Field(default_factory=list)
