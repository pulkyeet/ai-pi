from __future__ import annotations

import api.llm.tracing as tracing_module
from api.llm.tracing import LangfuseTracer, NoopTracer, Tracer, build_tracer


def _record(tracer: Tracer) -> None:
    tracer.record(
        run_id="run-1",
        task_id=None,
        prompt_id="echo",
        prompt_version="echo@abcd1234",
        model="deepseek/deepseek-v4-flash",
        variables={"message": "hi"},
        untrusted=None,
        output='{"message": "hi"}',
        input_tokens=10,
        output_tokens=5,
        cached_tokens=0,
        cost_usd=0.0001,
        latency_ms=42,
    )


def test_noop_tracer_never_raises() -> None:
    _record(NoopTracer())  # no assertion needed: not raising is the contract


def test_build_tracer_defaults_to_noop_when_unconfigured() -> None:
    tracer = build_tracer(public_key=None, secret_key=None, host="https://cloud.langfuse.com")
    assert isinstance(tracer, NoopTracer)


def test_build_tracer_returns_langfuse_tracer_when_configured() -> None:
    tracer = build_tracer(
        public_key="pk-test", secret_key="sk-test", host="https://cloud.langfuse.com"
    )
    assert isinstance(tracer, LangfuseTracer)


def test_langfuse_tracer_happy_path_does_not_raise() -> None:
    """The success path: `start_observation()` + `.end()` complete
    synchronously without touching the network (Langfuse batches export in a
    background thread), so this exercises `LangfuseTracer.record()` without
    needing a real reachable Langfuse project."""
    tracer = build_tracer(
        public_key="pk-test", secret_key="sk-test", host="https://cloud.langfuse.com"
    )
    assert isinstance(tracer, LangfuseTracer)
    _record(tracer)


def test_langfuse_outage_does_not_raise(monkeypatch) -> None:
    """A broken/unreachable Langfuse client must not fail a run — the
    guarantee lives in `LangfuseTracer.record()` itself, proven here by
    forcing the underlying client call to blow up."""
    tracer = build_tracer(
        public_key="pk-test", secret_key="sk-test", host="https://cloud.langfuse.com"
    )
    assert isinstance(tracer, LangfuseTracer)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("langfuse is down")

    monkeypatch.setattr(tracer._client, "start_observation", _boom)
    _record(tracer)  # must not raise


def test_build_tracer_falls_back_to_noop_when_construction_fails(monkeypatch) -> None:
    """A bad Langfuse config (e.g. a malformed host) must not block startup —
    `build_tracer` itself must never raise."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("bad config")

    monkeypatch.setattr(tracing_module, "Langfuse", _boom)
    tracer = build_tracer(public_key="pk-test", secret_key="sk-test", host="not-a-real-host")
    assert isinstance(tracer, NoopTracer)
