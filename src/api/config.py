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
