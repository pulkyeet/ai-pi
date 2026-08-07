from pydantic import PostgresDsn, SecretStr
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

    # Reddit (masterplan §13, D5): self-service registration is closed, so
    # this stays off until credentials exist. See api.sources.reddit.
    enable_reddit: bool = False
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None

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
