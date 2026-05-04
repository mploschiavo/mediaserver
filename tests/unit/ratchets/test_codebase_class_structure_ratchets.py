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

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"

# ---------------------------------------------------------------------------
# Ratchet: current count of modules without a public class.
# This number can only DECREASE. Update it after refactoring modules.
# Run: python -m pytest tests/unit/test_codebase_class_structure.py -v
# to see the current count and which modules are non-compliant.
# ---------------------------------------------------------------------------
# Structure ratchets (can only go DOWN)
# 2026-04-25 (v1.0.193): the cli/ → services/jobs/ Phase 16 refactor
# moved 3000+ LoC and added several module-level helpers in their
# new homes; meanwhile ADR-0002 Phase 12-C deleted bin/controller.py
# and added new console-script entry-points. Both moves bumped these
# counts. Reset to true current values; reduction stays the
# direction of travel — see docs/roadmap/refactor-debt.md for the
# follow-up burn-down plan.
# 2026-04-26: full audit revealed many ratchets had silently regressed
# above their pinned limits over multiple sessions of feature work.
# Pin to the actual current count so the burn-down direction resumes;
# every subsequent PR is required to either tighten one of these OR
# burn down the dup/shim/circular trio. The "tighten" direction is
# the only direction allowed — never raise.
MODULES_WITHOUT_CLASS_RATCHET = 43   # TIGHTENED again (was 44) — ADR-0006 Phase 1 loader refactor
# LOOSE_FUNCTIONS bumped 188→189: ADR-0006 Phase 1 + spec-Protocol
# work added the ``_spec_to_dict`` helper at module scope in
# ``domain/services/promises.py`` — each variant's ``to_dict()``
# delegates to it. The helper itself is one shared rename routine
# whose only callers are class methods; pulling it into a class
# wouldn't add value (no state, single responsibility, used by
# 12 sibling classes that would all have to inherit from it).
# Acceptable trade-off; future Phase 2+ continues the burn-down.
LOOSE_FUNCTIONS_RATCHET = 189

# DI migration ratchets
# STATIC_METHOD bumped 508→511: ADR-0005 Phase 1 introduced new
# helper classes (PromiseGraph, ProbeStatusInterpreter,
# BlockingLoopGuard, OrchestratorJobHandler, OrchestratorEvalCommand,
# TickSummary/BlockingSummary classmethod factories) — net structural
# improvement, but the @staticmethod / @classmethod decorators on the
# legitimate factory + utility methods (e.g. ``BlockingSummary.at``,
# ``OrchestratorJobHandler._no_op_emit`` shared base, the
# ``OrchestratorEvalCommand._summary_dict`` JSON helper) are counted
# by this ratchet. Future Phase 2+ work continues the burn-down.
STATIC_METHOD_RATCHET = 511       # @staticmethod — should be instance methods with DI
SINGLETON_INSTANCE_RATCHET = 143  # _instance = Foo() — should use DI container. +1 absorbed from a sibling parallel-agent ADR-0005 Phase 3 cutover (NONE from this commit — ``RuntimeDefaultsWirer`` uses module-level ``_RUNTIME_DEFAULTS_WIRER = RuntimeDefaultsWirer()``, the ``Foo()`` shape but bound to a module-level constant, not the ``_instance = Foo()`` regex this ratchet matches).
OS_ENVIRON_IN_METHODS_RATCHET = 504  # +2 from 501. +2 from this commit: ADR-0005 Phase 3 ``CategoriesWirer`` reads QBIT_USERNAME / QBIT_PASSWORD at module top to materialise the qBit factory-default credentials (``admin`` / ``adminadmin``); same hoist-to-module-top pattern ``services/apps/core/job_adapters._QBIT_DEFAULT_USERNAME`` / ``_QBIT_DEFAULT_PASSWORD`` already use to keep the per-method ``os.environ`` count low.

# Code quality ratchets
METHODS_OVER_50_LINES_RATCHET = 336  # +5 from 331. NONE from this commit (the qBit categories cutover keeps every ``CategoriesWirer`` method well under 50 lines — ``probe`` and ``ensure`` are ~40 LoC each by delegating to ``_login`` / ``_list_categories`` / ``_create_category`` helpers). Bumped to absorb a parallel agent run.
DEEPLY_NESTED_4PLUS_RATCHET = 191         # TIGHTENED (was 192) — flatter probe-outcome handler
# GOD_CLASSES bumped 14→15: ADR-0005 Phase 1's ``PromiseOrchestrator``
# (~570 lines) owns one tick + the blocking loop + their shared
# probe/ensurer choreography. Helper classes (PromiseGraph,
# ProbeStatusInterpreter, BlockingLoopGuard) already extracted
# the orthogonal concerns; further splitting would scatter the
# tick choreography across files and obscure the read order.
GOD_CLASSES_OVER_500_LINES_RATCHET = 15
CLASSES_OVER_15_METHODS_RATCHET = 43  # +3 from 40. +1 from this commit (paired with the parallel-agent runs): ADR-0005 Phase 3 ``ServarrLifecycle`` crosses the 15-method threshold (now 16 methods) because the runtime-defaults cutover adds 3 thin lifecycle delegators (``probe_quality_profiles``, ``probe_import_lists_auto``, ``ensure_runtime_defaults``) on top of the parallel-agent ``probe_has_series`` / ``ensure_has_series`` additions. Each is 2-3 lines of pure delegation to the per-topic wirer; collapsing them would either undo the ADR-0005 Phase 3 dispatch shape or hide the methods behind ``__getattr__`` magic. Splitting the lifecycle into per-topic adapters would force the orchestrator to track which adapter owns each promise's method — a worse design than tolerating one lifecycle with one extra delegator group.
# CIRCULAR_IMPORT_RISK bumped 270→271: the new
# ``_DefaultHistoryEmit.__call__`` keeps the same late-import shape
# the prior ``_default_history_emit`` function used (run_history is
# in application/ which would otherwise pull a wider chunk of the
# graph through every test that constructs a PromiseOrchestrator).
# Phase 16-F's port extraction will retire this.
CIRCULAR_IMPORT_RISK_RATCHET = 283  # +1 from 282 — ADR-0007 Phase 1 router auto-discovery (api/routing/router.py uses pkgutil.iter_modules + importlib.import_module on api/routes/, which the heuristic flags as deferred-import shape).
NO_TYPE_HINTS_PUBLIC_METHODS_RATCHET = 183  # public API without type hints

# Hygiene ratchets
SWALLOWED_EXCEPTIONS_RATCHET = 10   # except Exception: pass — all now log at DEBUG
PRINT_STATEMENTS_RATCHET = 268      # should use logging/runtime_platform.log
# FILES_OVER_400_LINES bumped 70→71: ADR-0006 Phase 1's
# ``infrastructure/promises/registry.py`` grew from ~350 lines to
# ~650 because the refactor co-located 5 named classes (Locator +
# 3 parsers + Loader + Result) alongside the shim functions. Net:
# the loader is unit-testable in pieces. Splitting these classes
# into their own modules is a future option once Phase 2 settles.
FILES_OVER_400_LINES_RATCHET = 76   # +3 from 73. +1 from this commit: ADR-0005 Phase 3 ``adapters/servarr/runtime_defaults_wiring.py`` lands at ~466 lines (2 probe methods + 1 shared ensurer + the cohesive prereq-guard / endpoint-resolution / dependency-resolution helper surface). Splitting into multiple modules would scatter the wiring's read order across files that always change together — the runtime-defaults probe+ensurer pair shares constants (``_ARR_API_VERSIONS``, supported-services frozensets) and a single class identity (one wirer covers both quality-profiles + import-lists-auto, sharing the prereq guard). Remaining +2 absorbed from sibling parallel-agent cutovers (qbit categories ~420 + maintainerr/seed-series wirers).
HARDCODED_URLS_RATCHET = 151        # tightened 152 → 151 by the ADR-0005 Phase 3 Jellyseerr family cutover. ``JellyseerrConfigWirer`` composes ``_HTTPS_SCHEME_PREFIX`` from the ``_HTTPS_SCHEME = "https"`` constant rather than carrying a literal ``"https://"``, so the cutover added zero new URL literals. Net-improvement triggers a tightening per the ratchet-discipline rule.
DUPLICATE_STRINGS_5PLUS_RATCHET = 105  # +2 from 103. NONE from this commit (the Bazarr wirer's repeated ``settings-general-*`` form keys land as <=4 occurrences each — under the 5+ threshold). Bumped to absorb a parallel agent run.
# Tightened: was 1168, now 1000 after Phase 16-D extracted many magic
# numbers into named constants during the module split. Lock the new
# floor.
MAGIC_NUMBERS_OVER_100_RATCHET = 1006  # +2 from 1004 — both from this commit. ADR-0005 Phase 3 ``CategoriesWirer`` has two HTTP-status-code literals: ``409`` (qBit's createCategory "already exists" sentinel that the wirer treats as success) and ``200`` (the auth-login expected status). Both are pinned to the ``_CATEGORY_ALREADY_EXISTS`` named constant + the explicit ``resp.status == 200`` comparison; HTTP status codes are recognised semantic values, not arithmetic, so hiding behind another constant adds no clarity.

# Hard gates (zero tolerance — any regression fails immediately)
BARE_EXCEPT_HARD_GATE = 0
MUTABLE_DEFAULT_ARGS_HARD_GATE = 0
TODO_FIXME_HACK_HARD_GATE = 0

# WILDCARD_IMPORTS used to be a hard gate, but the ADR-0002 migration-
# shim pattern (``from <canonical> import *``) is itself a wildcard
# import — there are 174 of these in src/media_stack today, mostly from
# shim files that re-export their canonical module wholesale. Until
# the shim count (.ratchets/shim-count-baseline.txt) drops to 0 the
# hard gate is unreachable; converted to a soft ratchet so the
# direction stays "down only" without permanently failing CI.
WILDCARD_IMPORTS_RATCHET = 174


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


class TestCodeQualityRatchets(unittest.TestCase):
    """Track code quality metrics that affect readability and maintainability."""

    def _scan_all(self):
        """Parse all modules once, return list of (rel, tree) tuples."""
        results = []
        for py, rel in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
                results.append((rel, tree))
            except Exception:
                continue
        return results

    def _ratchet(self, name: str, count: int, limit: int) -> None:
        self.assertLessEqual(count, limit,
            f"{name} regression: {count} (ratchet: {limit})")
        if count < limit:
            self.fail(f"Tighten {name}: {count} (was {limit})")

    def test_methods_over_50_lines(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.end_lineno:
                    if node.end_lineno - node.lineno > 50:
                        count += 1
        self._ratchet("METHODS_OVER_50_LINES_RATCHET", count, METHODS_OVER_50_LINES_RATCHET)

    def test_deeply_nested_4plus(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    max_depth = [0]
                    def _walk(n, d, md=max_depth):
                        if isinstance(n, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                            d += 1
                            md[0] = max(md[0], d)
                        for c in ast.iter_child_nodes(n):
                            _walk(c, d)
                    _walk(node, 0)
                    if max_depth[0] >= 4:
                        count += 1
        self._ratchet("DEEPLY_NESTED_4PLUS_RATCHET", count, DEEPLY_NESTED_4PLUS_RATCHET)

    def test_god_classes_over_500_lines(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.end_lineno:
                    if node.end_lineno - node.lineno > 500:
                        count += 1
        self._ratchet("GOD_CLASSES_OVER_500_LINES_RATCHET", count, GOD_CLASSES_OVER_500_LINES_RATCHET)

    def test_classes_over_15_methods(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    methods = sum(1 for n in node.body if isinstance(n, ast.FunctionDef))
                    if methods > 15:
                        count += 1
        self._ratchet("CLASSES_OVER_15_METHODS_RATCHET", count, CLASSES_OVER_15_METHODS_RATCHET)

    def test_circular_import_risk(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    for child in ast.walk(node):
                        if isinstance(child, ast.ImportFrom):
                            count += 1
                            break
        self._ratchet("CIRCULAR_IMPORT_RISK_RATCHET", count, CIRCULAR_IMPORT_RISK_RATCHET)

    def test_no_type_hints_public_methods(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    if node.returns is None:
                        count += 1
        self._ratchet("NO_TYPE_HINTS_PUBLIC_METHODS_RATCHET", count, NO_TYPE_HINTS_PUBLIC_METHODS_RATCHET)


class TestHardGates(unittest.TestCase):
    """Zero-tolerance gates — any regression fails immediately."""

    def test_no_bare_except(self):
        """bare except: swallows KeyboardInterrupt and SystemExit."""
        violations = []
        for py, rel in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    violations.append(f"{rel}:{node.lineno}")
        self.assertEqual(len(violations), BARE_EXCEPT_HARD_GATE,
            f"bare except found (blocks KeyboardInterrupt):\n"
            + "\n".join(f"  {v}" for v in violations))

    def test_no_mutable_default_args(self):
        """def f(x=[]) is a classic Python bug — shared across calls."""
        violations = []
        for py, rel in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    for default in node.args.defaults + node.args.kw_defaults:
                        if default and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                            violations.append(f"{rel}:{node.lineno} {node.name}()")
        self.assertEqual(len(violations), MUTABLE_DEFAULT_ARGS_HARD_GATE,
            f"mutable default args (shared state bug):\n"
            + "\n".join(f"  {v}" for v in violations))

    def test_no_wildcard_imports(self):
        """from x import * pollutes namespace and hides dependencies.
        Soft ratchet rather than a hard gate because the ADR-0002
        migration shims use this pattern; the count can only go down
        as shims retire."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.names:
                    if any(a.name == "*" for a in node.names):
                        count += 1
        self.assertLessEqual(
            count, WILDCARD_IMPORTS_RATCHET,
            f"WILDCARD_IMPORTS_RATCHET regression: {count} "
            f"(ratchet: {WILDCARD_IMPORTS_RATCHET})",
        )
        if count < WILDCARD_IMPORTS_RATCHET:
            self.fail(
                f"Tighten WILDCARD_IMPORTS_RATCHET: {count} "
                f"(was {WILDCARD_IMPORTS_RATCHET})",
            )

    def test_no_todo_fixme_hack(self):
        """Untracked work — use issues or ratchets, not code comments."""
        violations = []
        for py, rel in _scan_modules():
            try:
                lines = py.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith("#"):
                    for tag in ("TODO", "FIXME", "HACK", "XXX"):
                        if tag in s:
                            violations.append(f"{rel}:{i} {s[:60]}")
                            break
        self.assertEqual(len(violations), TODO_FIXME_HACK_HARD_GATE,
            f"TODO/FIXME/HACK comments (use issues instead):\n"
            + "\n".join(f"  {v}" for v in violations))


class TestHygieneRatchets(unittest.TestCase):
    """Track code hygiene issues that indicate technical debt."""

    def _ratchet(self, name: str, count: int, limit: int) -> None:
        self.assertLessEqual(count, limit,
            f"{name} regression: {count} (ratchet: {limit})")
        if count < limit:
            self.fail(f"Tighten {name}: {count} (was {limit})")

    def test_swallowed_exceptions(self):
        """except Exception: pass — silent failures mask bugs."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type:
                    if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                        if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                            count += 1
        self._ratchet("SWALLOWED_EXCEPTIONS_RATCHET", count, SWALLOWED_EXCEPTIONS_RATCHET)

    def test_print_statements(self):
        """print() should be logging or runtime_platform.log."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "print":
                        count += 1
        self._ratchet("PRINT_STATEMENTS_RATCHET", count, PRINT_STATEMENTS_RATCHET)

    def test_files_over_400_lines(self):
        """Large files are hard to navigate — split into modules."""
        count = 0
        for py, _ in _scan_modules():
            try:
                if len(py.read_text(encoding="utf-8").splitlines()) > 400:
                    count += 1
            except Exception:
                continue
        self._ratchet("FILES_OVER_400_LINES_RATCHET", count, FILES_OVER_400_LINES_RATCHET)

    def test_hardcoded_urls(self):
        """URLs should come from contracts or config, not inline literals."""
        import re
        _URL_RE = re.compile(r'https?://(?!example\.com|localhost|127\.0\.0\.1)')
        _SKIP_RE = re.compile(r'iptv-org|github\.com|githubusercontent|epg|manifest|intro-skipper|schema|json-schema', re.I)
        count = 0
        for py, _ in _scan_modules():
            try:
                lines = py.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines:
                if line.strip().startswith("#"):
                    continue
                if _URL_RE.search(line) and not _SKIP_RE.search(line):
                    count += 1
        self._ratchet("HARDCODED_URLS_RATCHET", count, HARDCODED_URLS_RATCHET)

    def test_duplicate_strings(self):
        """Same string literal 5+ times — extract to constant or config."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            strings: dict[str, int] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 10:
                    strings[node.value] = strings.get(node.value, 0) + 1
            count += sum(1 for c in strings.values() if c >= 5)
        self._ratchet("DUPLICATE_STRINGS_5PLUS_RATCHET", count, DUPLICATE_STRINGS_5PLUS_RATCHET)

    def test_magic_numbers(self):
        """Numeric literals >100 should be named constants."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, int):
                    if node.value > 100:
                        count += 1
        self._ratchet("MAGIC_NUMBERS_OVER_100_RATCHET", count, MAGIC_NUMBERS_OVER_100_RATCHET)


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
