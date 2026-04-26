"""Enforce class-based structure in the config service package.

Rules:
1. Each sub-module (_profile.py, _media_server.py, etc.) must define exactly one public class
2. No module-level mutable state (globals) outside __init__.py
3. No module-level functions (except private helpers that are static methods of the class)
4. No hardcoded data lists (>5 items) in Python — must come from YAML
5. __init__.py must only contain imports, singleton instances, and re-export assignments
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PKG = ROOT / "src" / "media_stack" / "api" / "services" / "config"


def _sub_modules() -> list[Path]:
    """Return service sub-modules (excludes __init__.py)."""
    return sorted(p for p in CONFIG_PKG.glob("_*.py") if p.name != "__init__.py")


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class TestEachSubModuleHasOneClass(unittest.TestCase):
    """Each _*.py sub-module must define exactly one public class."""

    def test_sub_modules_have_one_class(self):
        problems = []
        for py_file in _sub_modules():
            tree = _parse_module(py_file)
            classes = [
                node.name for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
            ]
            if len(classes) != 1:
                problems.append(f"{py_file.name}: expected 1 public class, found {len(classes)}: {classes}")
        self.assertFalse(problems, "Sub-modules with wrong class count:\n" + "\n".join(f"  - {p}" for p in problems))


class TestNoModuleLevelFunctions(unittest.TestCase):
    """Sub-modules should not have module-level function definitions.

    Helper logic should be static/class methods on the service class.
    """

    def test_no_top_level_functions_in_sub_modules(self):
        problems = []
        for py_file in _sub_modules():
            tree = _parse_module(py_file)
            top_level_funcs = [
                node.name for node in ast.iter_child_nodes(tree)
                if isinstance(node, ast.FunctionDef)
            ]
            if top_level_funcs:
                problems.append(f"{py_file.name}: module-level functions should be class methods: {top_level_funcs}")
        self.assertFalse(problems,
                         "Module-level functions found (move to class as static/classmethod):\n"
                         + "\n".join(f"  - {p}" for p in problems))


class TestNoModuleLevelMutableState(unittest.TestCase):
    """Sub-modules should not have module-level mutable assignments (except constants)."""

    def test_no_mutable_globals_in_sub_modules(self):
        """__init__.py is excluded — it's the wiring layer."""
        problems = []
        for py_file in _sub_modules():
            tree = _parse_module(py_file)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        name = ""
                        if isinstance(target, ast.Name):
                            name = target.id
                        if name and not name.startswith("_") and not name.isupper():
                            problems.append(f"{py_file.name}:{node.lineno}: mutable global '{name}'")
        self.assertFalse(problems,
                         "Mutable module-level state (use class attributes):\n"
                         + "\n".join(f"  - {p}" for p in problems))


class TestNoHardcodedDataLists(unittest.TestCase):
    """No inline data lists >5 items in Python — must come from YAML."""

    def test_no_large_inline_lists(self):
        problems = []
        for py_file in _sub_modules():
            tree = _parse_module(py_file)
            for node in ast.walk(tree):
                if isinstance(node, ast.List) and len(node.elts) > 5:
                    # Allow list comprehensions and variable assignments that are clearly code
                    problems.append(
                        f"{py_file.name}:{node.lineno}: inline list with {len(node.elts)} items "
                        f"(move to contracts/defaults/*.yaml)"
                    )
        self.assertFalse(problems,
                         "Hardcoded data lists found:\n"
                         + "\n".join(f"  - {p}" for p in problems))


class TestInitOnlyContainsWiring(unittest.TestCase):
    """__init__.py should only have imports, assignments, and singleton instantiation."""

    def test_no_functions_or_classes_in_init(self):
        init_path = CONFIG_PKG / "__init__.py"
        if not init_path.is_file():
            self.skipTest("No __init__.py")
        tree = _parse_module(init_path)
        funcs = [n.name for n in ast.iter_child_nodes(tree) if isinstance(n, ast.FunctionDef)]
        classes = [n.name for n in ast.iter_child_nodes(tree) if isinstance(n, ast.ClassDef)]
        self.assertFalse(funcs, f"__init__.py should not define functions: {funcs}")
        self.assertFalse(classes, f"__init__.py should not define classes: {classes}")


if __name__ == "__main__":
    unittest.main()
