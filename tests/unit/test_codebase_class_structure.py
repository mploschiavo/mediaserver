"""Enforce class-based architecture across the codebase.

Rules:
1. Every module should define at least one public class
2. No hardcoded data lists >5 items in config modules (must come from YAML)

This test uses a ratchet: it records the current violation count and fails
if it INCREASES. Refactoring modules reduces the count. The ratchet number
can only go down, never up — no allowlists, no exceptions.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"

# ---------------------------------------------------------------------------
# Ratchet: current count of modules without a public class.
# This number can only DECREASE. Update it after refactoring modules.
# Run: python -m pytest tests/unit/test_codebase_class_structure.py -v
# to see the current count and which modules are non-compliant.
# ---------------------------------------------------------------------------
MODULES_WITHOUT_CLASS_RATCHET = 133


def _scan_modules() -> list[tuple[Path, str]]:
    """Return (path, relative_name) for all non-init, non-private Python modules."""
    results = []
    for py in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(py) or py.name == "__init__.py":
            continue
        rel = str(py.relative_to(SRC))
        results.append((py, rel))
    return results


def _modules_without_class() -> list[str]:
    """Return modules that have public functions but no public class."""
    violations = []
    for py, rel in _scan_modules():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        classes = [n.name for n in ast.iter_child_nodes(tree)
                   if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
        funcs = [n.name for n in ast.iter_child_nodes(tree)
                 if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
        if funcs and not classes:
            violations.append(rel)
    return violations


class TestClassStructureRatchet(unittest.TestCase):
    """Module count without classes can only go DOWN, never up."""

    def test_no_new_modules_without_class(self):
        violations = _modules_without_class()
        count = len(violations)
        self.assertLessEqual(
            count, MODULES_WITHOUT_CLASS_RATCHET,
            f"\n{'=' * 70}\n"
            f"CLASS STRUCTURE REGRESSION: {count} modules without a public class\n"
            f"(ratchet allows {MODULES_WITHOUT_CLASS_RATCHET})\n"
            f"{'=' * 70}\n"
            f"New module(s) added without a class. Either:\n"
            f"  1. Add a class to the new module, or\n"
            f"  2. If you refactored other modules, update MODULES_WITHOUT_CLASS_RATCHET\n\n"
            f"Non-compliant modules ({count}):\n"
            + "\n".join(f"  {v}" for v in violations[:20])
            + (f"\n  ... and {count - 20} more" if count > 20 else ""),
        )

    def test_ratchet_is_tight(self):
        """Fail if the ratchet has room to tighten — forces update after refactoring."""
        violations = _modules_without_class()
        count = len(violations)
        if count < MODULES_WITHOUT_CLASS_RATCHET:
            self.fail(
                f"Ratchet is loose: {count} violations but ratchet allows "
                f"{MODULES_WITHOUT_CLASS_RATCHET}. Update MODULES_WITHOUT_CLASS_RATCHET "
                f"to {count} to lock in the improvement."
            )


class TestConfigModuleDataInYaml(unittest.TestCase):
    """Config sub-modules must not have inline data lists >5 items."""

    def test_no_hardcoded_data_in_config_modules(self):
        config_pkg = SRC / "api" / "services" / "config"
        if not config_pkg.is_dir():
            self.skipTest("config package not found")
        violations = []
        for py in sorted(config_pkg.glob("_*.py")):
            if py.name == "__init__.py":
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.List) and len(node.elts) > 5:
                    violations.append(f"{py.name}:{node.lineno}: list with {len(node.elts)} items")
        self.assertFalse(
            violations,
            f"Config modules must load data from YAML, not inline lists:\n"
            + "\n".join(f"  - {v}" for v in violations),
        )


if __name__ == "__main__":
    unittest.main()
