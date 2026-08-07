"""Prompt registry: versioned files, deterministic assembly, structural
untrusted-content containment (masterplan §6/§8.3, Phase 05).

Real prompt *content* for extraction/planning/synthesis belongs to the
phases that own it ([06](../../../docs/execution_phases/phase-06-claim-extraction-span-binding.md),
[09](../../../docs/execution_phases/phase-09-interpreter-planner.md),
[11](../../../docs/execution_phases/phase-11-synthesis-report-assembly.md)) —
out of this phase's scope by its own doc's "Out" list. `src/api/prompts/`
therefore stays empty here; this module's own tests point `PromptRegistry`
at a synthetic fixture directory (`tests/fixtures/prompts/`), the same way
Phase 02 hardened the executor against synthetic task kinds instead of
writing throwaway domain content just to exercise the loader.

File format::

    ---
    id: example
    schema: ExampleResult
    cache_prefix_ends_after: instructions
    ---
    ## role
    ...static, becomes part of the cached system prefix...

    ## instructions
    ...also static...

    ## user
    ...templated with {{double_brace}} variables, sent as the user message...

`schema` is informational only (a human-readable label for the Pydantic
model the prompt targets) — the actual `type[T]` enforced at call time comes
from the caller of `structured()`, not from this file, so this module never
needs to import domain schema classes.

Everything up to and including the `cache_prefix_ends_after` section is the
static system prefix (masterplan §6's cache-optimal ordering: the largest
repeated segment goes first, right up to the cache breakpoint). Sections
after it form the per-call user template.

Untrusted content never goes through `{{var}}` substitution — it is appended
as its own delimited block *after* rendering, so there is no template
control flow for it to participate in (the substitution mechanism here has
none to begin with — it is a single regex replace, not a template language).
The delimiter is a fixed `<untrusted name="...">...</untrusted>` tag; `&`/
`<`/`>` inside the content are always entity-escaped first, so no input —
including one that literally contains the string `</untrusted>` — can ever
produce a real closing tag.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_SECTION_RE = re.compile(r"^##\s+(\S+)\s*$", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class PromptTemplate(BaseModel):
    id: str
    version: str
    schema_hint: str
    static_prefix: str
    user_template: str
    source_path: str


class PromptParseError(Exception):
    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(f"{path}: {detail}")


class PromptNotFoundError(Exception):
    def __init__(self, prompt_id: str) -> None:
        super().__init__(f"no prompt registered with id {prompt_id!r}")
        self.prompt_id = prompt_id


class PromptRenderError(Exception):
    def __init__(self, prompt_id: str, detail: str) -> None:
        super().__init__(f"{prompt_id}: {detail}")


def _split_frontmatter(path: Path, raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---\n"):
        raise PromptParseError(path, "must start with a YAML frontmatter block ('---')")
    end = raw.find("\n---", 4)
    if end == -1:
        raise PromptParseError(path, "unterminated frontmatter block")
    frontmatter_text = raw[4:end]
    body = raw[end + 4 :].lstrip("\n")
    loaded = yaml.safe_load(frontmatter_text) or {}
    if not isinstance(loaded, dict):
        raise PromptParseError(path, "frontmatter must be a YAML mapping")
    return loaded, body


def _split_sections(path: Path, body: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        raise PromptParseError(path, "body must contain at least one '## section' heading")
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name] = body[start:end].strip()
    return sections


def _parse_prompt_file(path: Path) -> PromptTemplate:
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(path, raw)

    for key in ("id", "schema", "cache_prefix_ends_after"):
        if key not in frontmatter:
            raise PromptParseError(path, f"frontmatter missing required key {key!r}")

    prompt_id = str(frontmatter["id"])
    schema_hint = str(frontmatter["schema"])
    breakpoint_name = str(frontmatter["cache_prefix_ends_after"])

    sections = _split_sections(path, body)
    if breakpoint_name not in sections:
        raise PromptParseError(
            path,
            f"cache_prefix_ends_after={breakpoint_name!r} names no '## {breakpoint_name}' section",
        )

    names = list(sections)
    split_at = names.index(breakpoint_name) + 1
    static_prefix = "\n\n".join(sections[n] for n in names[:split_at])
    user_template = "\n\n".join(sections[n] for n in names[split_at:])

    version = f"{prompt_id}@{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"

    return PromptTemplate(
        id=prompt_id,
        version=version,
        schema_hint=schema_hint,
        static_prefix=static_prefix,
        user_template=user_template,
        source_path=str(path),
    )


class PromptRegistry:
    """Loads every `*.md` file in `directory` at construction time. A
    registry is a snapshot: editing a file on disk after construction has no
    effect until a new `PromptRegistry` is built (matches Phase 04's
    `SearchRouter` — no caller-visible hot-reload magic)."""

    def __init__(self, directory: Path = PROMPTS_DIR) -> None:
        self._templates: dict[str, PromptTemplate] = {}
        for path in sorted(Path(directory).glob("*.md")):
            template = _parse_prompt_file(path)
            if template.id in self._templates:
                raise PromptParseError(path, f"duplicate prompt id {template.id!r}")
            self._templates[template.id] = template

    def get(self, prompt_id: str) -> PromptTemplate:
        try:
            return self._templates[prompt_id]
        except KeyError:
            raise PromptNotFoundError(prompt_id) from None

    def __iter__(self) -> Iterator[PromptTemplate]:
        return iter(self._templates.values())

    def __len__(self) -> int:
        return len(self._templates)


def _substitute(prompt_id: str, template: str, variables: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise PromptRenderError(prompt_id, f"missing variable {name!r}")
        return variables[name]

    return _PLACEHOLDER_RE.sub(replace, template)


def _escape_untrusted(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_messages(
    template: PromptTemplate,
    variables: Mapping[str, str],
    untrusted: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    """Assemble the OpenRouter `messages` array. `messages[0]` (system) is a
    pure function of `template` alone — never of `variables`/`untrusted` —
    which is what makes the prefix byte-identical across calls and therefore
    cacheable (masterplan §6)."""
    messages: list[dict[str, str]] = [{"role": "system", "content": template.static_prefix}]

    user_parts: list[str] = []
    if template.user_template:
        user_parts.append(_substitute(template.id, template.user_template, variables))
    if untrusted:
        blocks = "\n\n".join(
            f'<untrusted name="{name}">\n{_escape_untrusted(content)}\n</untrusted>'
            for name, content in untrusted.items()
        )
        user_parts.append(blocks)

    if user_parts:
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})

    return messages


__all__ = [
    "PROMPTS_DIR",
    "PromptNotFoundError",
    "PromptParseError",
    "PromptRegistry",
    "PromptRenderError",
    "PromptTemplate",
    "render_messages",
]
