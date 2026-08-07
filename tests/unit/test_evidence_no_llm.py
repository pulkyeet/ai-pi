"""Phase 08 exit criterion: "Zero LLM calls in this phase". Grading,
confidence, contradictions, promotion and coverage are all arithmetic or
SQL over already-typed claims — masterplan §4.6/§4.7 are explicit that this
is what makes the phase deterministic. Enforced structurally (an AST import
check), not just by convention, matching `api.sources.serp_snippets`'s own
"no `httpx` import at all" test for the same kind of structural guarantee.
"""

from __future__ import annotations

import ast
from pathlib import Path

import api.evidence


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names |= {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    return names


def test_no_module_in_api_evidence_imports_api_llm() -> None:
    package_dir = Path(api.evidence.__file__).parent
    for path in sorted(package_dir.glob("*.py")):
        imported = _imported_modules(path)
        offending = {m for m in imported if m == "api.llm" or m.startswith("api.llm.")}
        assert not offending, f"{path.name} imports {offending}, but Phase 08 must call no LLM"
