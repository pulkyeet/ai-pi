from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from pydantic import ValidationError

from api.config import Settings
from api.logging import configure_logging, configure_tracing

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/ai_pi",
    "OPENROUTER_API_KEY": "x",
    "EXA_API_KEY": "x",
    "GITHUB_TOKEN": "x",
}


def test_settings_raises_on_missing_required_value(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (*_REQUIRED_ENV, "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_loads_with_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert str(settings.database_url).startswith("postgresql://")


def test_configure_tracing_emits_a_span(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    configure_logging(settings)
    provider = configure_tracing(settings)
    assert isinstance(provider, TracerProvider)

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test.span") as span:
        assert span.is_recording()
