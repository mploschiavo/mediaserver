"""ADR-0011 Phase 1 ratchet — domain/core leaf invariant.

The hexagonal layer DAG points inward only:

    domain  /  core   <-  application  <-  adapters/infrastructure  <-  api/cli

That direction is enforced at module-load time by what each layer is
allowed to ``import``. The classic escape hatch — a deferred
``from media_stack.<outer-layer> import …`` inside a function body —
silently inverts the dependency at call time and reintroduces the
cycle the hexagon was supposed to forbid.

This module pins the count of those inversions in ``domain/`` and
``core/``:

* ``domain/`` is held at **zero** (ADR-0011 Phase 1 closed the last
  two: ``Job.run`` reading ``services.runtime_platform.log`` and
  ``secret_scrub`` reaching into ``services.media_integrity.adapters``
  for the ``ServarrHttpError`` shape).
* ``core/`` is held at **one** — the lone surviving offender is
  ``catalog_loader._enrich_apps_from_registry`` reaching into
  ``api.services.registry``. Phase 2 of ADR-0011 relocates
  ``api.services.registry`` into ``application/service_registry/``,
  at which point this ratchet tightens to zero.

Why a separate test instead of folding into
``CIRCULAR_IMPORT_RISK_RATCHET``? That ratchet counts *all* deferred
imports, including the legitimate ones that break framework-internal
cycles inside the application/adapters layers. The leaf invariant is
narrower and binary — a deferred outward import from a leaf layer is
*never* legitimate, so it deserves its own pin.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "media_stack"

_LEAF_LAYERS = ("domain", "core")
_NON_LEAF_LAYERS = frozenset({
    "application",
    "adapters",
    "infrastructure",
    "api",
    "cli",
    "services",
    "interfaces",
})

_DOMAIN_LEAF_VIOLATIONS_RATCHET = 0
_CORE_LEAF_VIOLATIONS_RATCHET = 1


class TestDomainCoreLeafInvariant(unittest.TestCase):
    """Function-body imports out of ``domain/`` and ``core/`` are banned."""

    def _scan_layer(self, layer: str) -> list[tuple[str, str, str, int]]:
        layer_dir = _SRC_ROOT / layer
        violations: list[tuple[str, str, str, int]] = []
        for py in layer_dir.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom) and child.module:
                        module = child.module
                        if self._is_inverted(module):
                            violations.append((
                                str(py.relative_to(_SRC_ROOT)),
                                node.name,
                                module,
                                child.lineno,
                            ))
                    elif isinstance(child, ast.Import):
                        for alias in child.names:
                            if self._is_inverted(alias.name):
                                violations.append((
                                    str(py.relative_to(_SRC_ROOT)),
                                    node.name,
                                    alias.name,
                                    child.lineno,
                                ))
        return violations

    @staticmethod
    def _is_inverted(module: str) -> bool:
        if not module.startswith("media_stack."):
            return False
        parts = module.split(".")
        if len(parts) < 2:
            return False
        return parts[1] in _NON_LEAF_LAYERS

    def _ratchet(self, layer: str, limit: int) -> None:
        violations = self._scan_layer(layer)
        count = len(violations)
        if count > limit:
            details = "\n  ".join(
                f"{rel}::{fn}() L{line} -> {mod}"
                for rel, fn, mod, line in violations
            )
            self.fail(
                f"{layer}/ leaf invariant regressed: {count} deferred "
                f"outward import(s) found (ratchet: {limit}).\n  {details}\n"
                "Fix the inversion (move the imported symbol into a leaf "
                "layer, or move the importing module out of the leaf)."
            )
        if count < limit:
            self.fail(
                f"Tighten {layer}/ leaf ratchet: count is {count} "
                f"(was pinned at {limit})."
            )

    def test_domain_has_zero_inverted_imports(self) -> None:
        self._ratchet("domain", _DOMAIN_LEAF_VIOLATIONS_RATCHET)

    def test_core_has_only_known_inverted_imports(self) -> None:
        self._ratchet("core", _CORE_LEAF_VIOLATIONS_RATCHET)


if __name__ == "__main__":
    unittest.main()
