"""Ratchet: no function-scoped re-imports of names already imported at
module level inside route handler methods.

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

ADR-0007 Phase E retired ``handlers_get.py`` (and its giant
``handle()`` elif chain); the equivalent dispatch now lives across
the ``api/routes/*.py`` modules whose RouteModule-decorated handler
methods each replace one branch. The local-shadow trap is identical
in shape — a function-scoped re-import of a module-level name still
triggers UnboundLocalError on the first branch that touches the
name. This ratchet now scans every method on every RouteModule for
the same forbidden local imports.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


# Names that are imported at module level in many route modules and
# must never be re-imported inside a handler method body. Extending
# this set has zero ratchet cost and prevents the bug class from
# re-emerging under a different name pair.
FORBIDDEN_LOCAL_IMPORTS: dict[str, set[str]] = {
    "urllib.parse": {"urlparse", "parse_qs", "urlencode", "quote", "unquote"},
}


# Post-Phase-E: route methods live under api/routes/. The legacy
# ``handle()`` function is gone, so every handler method on a
# RouteModule subclass is in scope.
ROUTES_DIR = ROOT / "src" / "media_stack" / "api" / "routes"


def _module_level_imports_for(
    tree: ast.Module,
) -> dict[str, set[str]]:
    """Collect ``from X import Y`` at module level — that's the set
    a function-local re-import would shadow."""
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                out.setdefault(mod, set()).add(alias.name)
    return out


class _LocalShadowVisitor(ast.NodeVisitor):
    """Walks a function body and records any ``from X import Y``
    where ``X`` is in FORBIDDEN_LOCAL_IMPORTS, ``Y`` is in the
    allowlist for that module, AND ``Y`` is also imported at module
    level (otherwise a local import is the canonical introduction,
    not a shadow)."""

    def __init__(self, module_imports: dict[str, set[str]]) -> None:
        # (lineno, module, name) tuples.
        self.violations: list[tuple[int, str, str]] = []
        self._module_imports = module_imports

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        forbidden = FORBIDDEN_LOCAL_IMPORTS.get(module)
        if forbidden:
            module_already_imports = self._module_imports.get(module, set())
            for alias in node.names:
                if (alias.name in forbidden
                        and alias.name in module_already_imports):
                    self.violations.append(
                        (node.lineno, module, alias.name),
                    )
        # Continue walking — nested defs get the same treatment.
        self.generic_visit(node)


class NoLocalShadowImportsInHandleRatchet(unittest.TestCase):
    """Asserts no handler method in any RouteModule re-imports a name
    that's already a module-level import. Prevents the v1.0.239
    timeseries-500 bug class from re-emerging."""

    def test_handle_function_has_no_forbidden_local_imports(self) -> None:
        self.assertTrue(
            ROUTES_DIR.is_dir(),
            f"routes directory not found: {ROUTES_DIR}",
        )
        all_violations: list[tuple[Path, int, str, str]] = []
        scanned_files = 0
        for source_file in sorted(ROUTES_DIR.rglob("*.py")):
            if source_file.name.startswith("test_"):
                continue
            try:
                src = source_file.read_text(encoding="utf-8")
                tree = ast.parse(src)
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            scanned_files += 1
            module_imports = _module_level_imports_for(tree)
            # Walk every method body — a route module's handler
            # methods are FunctionDef nodes inside a class body.
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                visitor = _LocalShadowVisitor(module_imports)
                for stmt in node.body:
                    visitor.visit(stmt)
                for ln, mod, name in visitor.violations:
                    all_violations.append((source_file, ln, mod, name))
        self.assertGreater(
            scanned_files, 0,
            "ratchet scanned no route modules — the routes directory "
            "is empty or unreadable.",
        )
        if all_violations:
            details = "\n".join(
                f"  {p.relative_to(ROOT)}:{ln}: from {mod} import {name}"
                for p, ln, mod, name in all_violations
            )
            self.fail(
                "Found function-local re-imports that shadow a "
                "module-level import — this triggers UnboundLocalError "
                "on any branch that references the name before the "
                "local import line:\n"
                f"{details}\n"
                "Fix: delete the redundant local import. The names are "
                "already at module level.",
            )


if __name__ == "__main__":
    unittest.main()
