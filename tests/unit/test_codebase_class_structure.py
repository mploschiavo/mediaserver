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
MODULES_WITHOUT_CLASS_RATCHET = 0
LOOSE_FUNCTIONS_RATCHET = 70
STATIC_METHOD_RATCHET = 415       # @staticmethod — should be instance methods with DI
SINGLETON_INSTANCE_RATCHET = 133  # _instance = Foo() — should use DI container
OS_ENVIRON_IN_METHODS_RATCHET = 367  # os.environ in methods — should be config injection


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


def _modules_with_loose_functions() -> list[str]:
    """Return modules that have ANY top-level function definitions (public or private)."""
    violations = []
    for py, rel in _scan_modules():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        loose_funcs = [n.name for n in ast.iter_child_nodes(tree)
                       if isinstance(n, ast.FunctionDef)]
        if loose_funcs:
            violations.append(f"{rel} ({', '.join(loose_funcs[:5])}{'...' if len(loose_funcs) > 5 else ''})")
    return violations


class TestClassStructureRatchet(unittest.TestCase):
    """No module-level functions anywhere — all logic must live in classes."""

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

    def test_no_loose_functions(self):
        """No module-level function defs — all logic in classes."""
        violations = _modules_with_loose_functions()
        count = len(violations)
        self.assertLessEqual(
            count, LOOSE_FUNCTIONS_RATCHET,
            f"\n{'=' * 70}\n"
            f"LOOSE FUNCTION REGRESSION: {count} modules with module-level functions\n"
            f"(ratchet allows {LOOSE_FUNCTIONS_RATCHET})\n"
            f"{'=' * 70}\n"
            f"Move functions into the class as methods or static methods.\n\n"
            + "\n".join(f"  {v}" for v in violations[:20])
            + (f"\n  ... and {count - 20} more" if count > 20 else ""),
        )

    def test_loose_functions_ratchet_is_tight(self):
        violations = _modules_with_loose_functions()
        count = len(violations)
        if count < LOOSE_FUNCTIONS_RATCHET:
            self.fail(
                f"Ratchet is loose: {count} modules with loose functions but ratchet allows "
                f"{LOOSE_FUNCTIONS_RATCHET}. Update LOOSE_FUNCTIONS_RATCHET to {count}."
            )


class TestOOPQualityRatchets(unittest.TestCase):
    """Track anti-patterns that prevent proper dependency injection."""

    def _count_static_methods(self) -> int:
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for cls in ast.walk(tree):
                if not isinstance(cls, ast.ClassDef):
                    continue
                for node in cls.body:
                    if isinstance(node, ast.FunctionDef):
                        for dec in node.decorator_list:
                            if isinstance(dec, ast.Name) and dec.id == "staticmethod":
                                count += 1
        return count

    def _count_singleton_instances(self) -> int:
        count = 0
        for py, _ in _scan_modules():
            try:
                text = py.read_text(encoding="utf-8")
            except Exception:
                continue
            if "_instance = " in text and "()" in text:
                count += 1
        return count

    def _count_os_environ_refs(self) -> int:
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
                    if hasattr(node.value, "attr") and node.value.attr == "environ":
                        count += 1
        return count

    def test_static_methods_ratchet(self):
        """@staticmethod should become instance methods with proper DI."""
        count = self._count_static_methods()
        self.assertLessEqual(count, STATIC_METHOD_RATCHET,
            f"@staticmethod regression: {count} (ratchet: {STATIC_METHOD_RATCHET})")
        if count < STATIC_METHOD_RATCHET:
            self.fail(f"Tighten STATIC_METHOD_RATCHET: {count} (was {STATIC_METHOD_RATCHET})")

    def test_singleton_instances_ratchet(self):
        """_instance = Foo() singletons should become DI-managed services."""
        count = self._count_singleton_instances()
        self.assertLessEqual(count, SINGLETON_INSTANCE_RATCHET,
            f"Singleton regression: {count} (ratchet: {SINGLETON_INSTANCE_RATCHET})")
        if count < SINGLETON_INSTANCE_RATCHET:
            self.fail(f"Tighten SINGLETON_INSTANCE_RATCHET: {count} (was {SINGLETON_INSTANCE_RATCHET})")

    def test_os_environ_in_methods_ratchet(self):
        """os.environ in methods should become constructor-injected config."""
        count = self._count_os_environ_refs()
        self.assertLessEqual(count, OS_ENVIRON_IN_METHODS_RATCHET,
            f"os.environ regression: {count} (ratchet: {OS_ENVIRON_IN_METHODS_RATCHET})")
        if count < OS_ENVIRON_IN_METHODS_RATCHET:
            self.fail(f"Tighten OS_ENVIRON_IN_METHODS_RATCHET: {count} (was {OS_ENVIRON_IN_METHODS_RATCHET})")


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
