"""Ratchet: no function-scoped re-imports of names already imported at
module level inside ``handlers_get.handle()``.

Why this exists: the v1.0.240 hotfix patched a class of bug where two
``elif`` branches inside the giant ``handle()`` function did
``from urllib.parse import urlparse, parse_qs`` inside try-blocks.
Python's name resolution treats *any* binding inside a function body
(including imports) as a function-local for the entire scope, so
later branches that referenced ``parse_qs`` from the module-level
import raised ``UnboundLocalError`` even though the import obviously
existed at the top of the file.

Symptom: ``GET /api/envoy/timeseries`` (added in v1.0.239) returned
500 errors on every poll because its ``parse_qs(urlparse(...).query)``
call hit the local-shadow trap. The Routing tab's live request-rate
chart and sparklines silently never populated.

The fix removed the redundant local imports. This ratchet flags any
future re-import that could re-introduce the bug.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


# Names that are imported at module level in handlers_get.py and must
# never be re-imported inside the body of ``handle()``. Extending this
# set has zero ratchet cost and prevents the bug class from re-emerging
# under a different name pair.
FORBIDDEN_LOCAL_IMPORTS: dict[str, set[str]] = {
    "urllib.parse": {"urlparse", "parse_qs", "urlencode", "quote", "unquote"},
}

HANDLERS_GET = ROOT / "src" / "media_stack" / "api" / "handlers_get.py"


class _LocalShadowVisitor(ast.NodeVisitor):
    """Walks a function body and records any ``from X import Y``
    where ``X`` is in FORBIDDEN_LOCAL_IMPORTS and ``Y`` is in the
    allowlist for that module."""

    def __init__(self) -> None:
        # (lineno, module, name) tuples.
        self.violations: list[tuple[int, str, str]] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        forbidden = FORBIDDEN_LOCAL_IMPORTS.get(module)
        if forbidden:
            for alias in node.names:
                if alias.name in forbidden:
                    self.violations.append(
                        (node.lineno, module, alias.name),
                    )
        # Continue walking — nested defs inside handle() (none today,
        # but future-proofing) get the same treatment.
        self.generic_visit(node)


class NoLocalShadowImportsInHandleRatchet(unittest.TestCase):
    """Asserts handle() doesn't re-import any name that's already a
    module-level import. Prevents the v1.0.239 timeseries-500 bug
    class from re-emerging."""

    def test_handle_function_has_no_forbidden_local_imports(self) -> None:
        src = HANDLERS_GET.read_text(encoding="utf-8")
        tree = ast.parse(src)
        # Find the GetHandler.handle method (or top-level handle()).
        target_funcs: list[ast.FunctionDef] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "handle":
                target_funcs.append(node)
        self.assertTrue(
            target_funcs,
            "Couldn't locate handle() in handlers_get.py — the test "
            "module path is probably stale. Update HANDLERS_GET.",
        )
        all_violations: list[tuple[int, str, str]] = []
        for fn in target_funcs:
            visitor = _LocalShadowVisitor()
            for stmt in fn.body:
                visitor.visit(stmt)
            all_violations.extend(visitor.violations)
        if all_violations:
            details = "\n".join(
                f"  line {ln}: from {mod} import {name}"
                for ln, mod, name in all_violations
            )
            self.fail(
                "Found function-local re-imports inside handle() that "
                "shadow module-level imports — this triggers "
                "UnboundLocalError on any branch that references the "
                "name before the local import line:\n"
                f"{details}\n"
                "Fix: delete the redundant local import. The names are "
                "already at module level.",
            )


if __name__ == "__main__":
    unittest.main()
