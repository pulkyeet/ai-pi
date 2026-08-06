"""Phase 01 spike: LLM validation for deepseek/deepseek-v4-flash via OpenRouter.

Verifies three things the masterplan (§6) asserts on faith:
1. Structured output actually holds — schema violation rate with and without
   `provider.require_parameters: true`.
2. Prompt caching works and pays — fire a static-prefix request 10 times,
   read back `usage.prompt_tokens_details.cached_tokens`.
3. Real cost per extraction call — run real page text through a realistic
   extraction prompt, extrapolate to a 60-page run, compare to the
   masterplan's ~$0.03/run estimate.

Requires OPENROUTER_API_KEY in the environment (see .env). Real cost: well
under a cent for the whole spike at deepseek-v4-flash's per-token pricing —
verified against the model's live /models pricing before running.

Run: uv run python spikes/llm_openrouter.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import trafilatura

from _common import hr, vcr_cassette

MODEL = "deepseek/deepseek-v4-flash"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
PAGES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pages"
MANIFEST_PATH = PAGES_DIR / "manifest.json"

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "plan_name": {"type": "string"},
        "price_usd": {"type": ["number", "null"]},
        "billing_period": {"type": "string", "enum": ["month", "year", "one_time", "unknown"]},
    },
    "required": ["plan_name", "price_usd", "billing_period"],
    "additionalProperties": False,
}

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "plan_extraction", "strict": True, "schema": PLAN_SCHEMA},
}


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}


def chat(
    client: httpx.Client, messages: list[dict], *, require_parameters: bool = False
) -> httpx.Response:
    body: dict = {
        "model": MODEL,
        "messages": messages,
        "response_format": RESPONSE_FORMAT,
        "usage": {"include": True},
    }
    if require_parameters:
        body["provider"] = {"require_parameters": True}
    return client.post(CHAT_URL, headers=headers(), json=body, timeout=30)


def load_page_snippets(n: int) -> list[str]:
    manifest = json.loads(MANIFEST_PATH.read_text())
    snippets = []
    for entry in manifest:
        if not entry.get("fetched"):
            continue
        html_path = PAGES_DIR / f"{entry['domain'].replace('.', '_')}.html"
        if not html_path.exists():
            continue
        text = trafilatura.extract(html_path.read_text(encoding="utf-8", errors="replace")) or ""
        if text:
            snippets.append(text[:1200])
    # cycle to reach n if the corpus is smaller
    return [snippets[i % len(snippets)] for i in range(n)]


def is_schema_valid(content: str) -> bool:
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(obj, dict):
        return False
    if set(obj.keys()) - {"plan_name", "price_usd", "billing_period"}:
        return False
    if not isinstance(obj.get("plan_name"), str):
        return False
    if obj.get("price_usd") is not None and not isinstance(obj.get("price_usd"), (int, float)):
        return False
    if obj.get("billing_period") not in {"month", "year", "one_time", "unknown"}:
        return False
    return True


def structured_output_test(client: httpx.Client, snippets: list[str], require_parameters: bool) -> dict:
    violations = 0
    for text in snippets:
        messages = [
            {"role": "system", "content": "Extract one pricing plan mentioned in the text as JSON."},
            {"role": "user", "content": text},
        ]
        r = chat(client, messages, require_parameters=require_parameters)
        if r.status_code != 200:
            violations += 1
            continue
        content = r.json()["choices"][0]["message"]["content"]
        if not is_schema_valid(content):
            violations += 1
    return {"n": len(snippets), "violations": violations, "require_parameters": require_parameters}


def caching_test(client: httpx.Client, n: int = 10) -> list[dict]:
    static_prefix = (
        "You are a pricing extraction engine. Follow this schema strictly: "
        + json.dumps(PLAN_SCHEMA)
        + "\n\nHere is background context repeated across calls to test prompt caching: "
        + ("The quick brown fox jumps over the lazy dog. " * 150)
    )
    rows = []
    for i in range(n):
        messages = [
            {"role": "system", "content": static_prefix},
            {"role": "user", "content": f"Call number {i}. The Basic plan is $9/month."},
        ]
        r = chat(client, messages)
        usage = r.json().get("usage", {})
        details = usage.get("prompt_tokens_details", {})
        rows.append(
            {
                "call": i,
                "prompt_tokens": usage.get("prompt_tokens"),
                "cached_tokens": details.get("cached_tokens", 0),
                "cost_usd": usage.get("cost"),
            }
        )
    return rows


def real_cost_test(client: httpx.Client, snippets: list[str]) -> list[dict]:
    rows = []
    for text in snippets:
        messages = [
            {
                "role": "system",
                "content": "Extract every distinct pricing plan mentioned in the text as a JSON "
                "array of objects, each matching this schema: " + json.dumps(PLAN_SCHEMA),
            },
            {"role": "user", "content": text},
        ]
        body = {
            "model": MODEL,
            "messages": messages,
            "usage": {"include": True},
        }
        r = client.post(CHAT_URL, headers=headers(), json=body, timeout=30)
        usage = r.json().get("usage", {})
        rows.append(
            {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "cost_usd": usage.get("cost"),
            }
        )
    return rows


def main() -> None:
    with vcr_cassette("llm_openrouter"), httpx.Client() as client:
        hr("Confirm model + pricing available")
        r = client.get("https://openrouter.ai/api/v1/models", headers=headers(), timeout=15)
        models = {m["id"]: m for m in r.json()["data"]}
        assert MODEL in models, f"{MODEL} not found in OpenRouter's model list"
        print(f"{MODEL} pricing: {models[MODEL]['pricing']}")

        snippets_50 = load_page_snippets(50)

        hr("1. Structured output — 50 calls WITHOUT provider pinning")
        without = structured_output_test(client, snippets_50, require_parameters=False)
        print(without)

        hr("1. Structured output — 50 calls WITH require_parameters=true")
        with_pin = structured_output_test(client, snippets_50, require_parameters=True)
        print(with_pin)

        hr("2. Prompt caching — same static prefix fired 10 times")
        cache_rows = caching_test(client, n=10)
        for row in cache_rows:
            print(row)
        first, rest = cache_rows[0], cache_rows[1:]
        avg_rest_cached = sum(r["cached_tokens"] for r in rest) / len(rest) if rest else 0
        print(f"\nfirst call cached_tokens={first['cached_tokens']}, avg of remaining 9={avg_rest_cached:.0f}")

        hr("3. Real cost per extraction call — 20 real pages")
        snippets_20 = load_page_snippets(20)
        cost_rows = real_cost_test(client, snippets_20)
        total_cost = sum(r["cost_usd"] or 0 for r in cost_rows)
        total_prompt = sum(r["prompt_tokens"] or 0 for r in cost_rows)
        total_completion = sum(r["completion_tokens"] or 0 for r in cost_rows)
        print(f"20-page total: ${total_cost:.6f}, prompt_tokens={total_prompt}, completion_tokens={total_completion}")
        per_page = total_cost / len(cost_rows)
        print(f"cost per page: ${per_page:.6f}")
        print(f"extrapolated to 60-page run: ${per_page * 60:.6f} (masterplan estimated $0.03)")


if __name__ == "__main__":
    main()
