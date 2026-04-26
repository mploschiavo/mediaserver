"""Ratchet: ``_dispatch_action`` must stay a single-line dispatch
through ``run_job``, not accrue per-action elif branches.

Background: before the unification, ``_dispatch_action`` was a
ladder of ``elif action_name == "post-setup": action_post_setup(...)``
branches — one per action. Every new action meant adding another
branch, and the job tree never knew about half of them. The unified
form has the dispatch body do exactly:

    if action_name == "reconcile": ... alias to bootstrap
    runtime_platform.log(...)
    result = run_job(action_name, ...)
    if not result: raise ValueError(...)
    if result.get("error"): raise RuntimeError(...)

This file fails fast if anyone reintroduces a per-action handler
branch by string-matching the dispatch body for action-name string
literals beyond the one alias.

The ``reconcile`` alias is the *only* permitted special case; it
trades a multi-line wrapper for a one-line ``action_name = "bootstrap"``
relabel. Any other ``action_name == "..."`` branch is a regression."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


_DISPATCH_PATH = (
    ROOT / "src" / "media_stack" / "cli" / "commands"
    / "controller_dispatch.py"
)

# Action-name special cases are now declared in the contract
# (``plugin.job_aliases`` in ``contracts/services/*.yaml``), not in
# the dispatch. Adding ANY string-comparison against ``action_name``
# here is a regression — declare an alias instead.
_ALLOWED_ACTION_NAME_LITERALS: frozenset[str] = frozenset()


def _dispatch_function_node() -> ast.FunctionDef:
    tree = ast.parse(_DISPATCH_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_dispatch_action":
            return node
    raise AssertionError("_dispatch_action function not found in "
                         "controller_dispatch.py")


def _string_literals_compared_with_action_name(
    fn: ast.FunctionDef,
) -> set[str]:
    """Return every string literal that appears on either side of an
    ``action_name == "..."`` comparison inside ``fn``. These are the
    per-action special cases the ratchet polices."""
    literals: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        names = [s for s in sides if isinstance(s, ast.Name)]
        constants = [s for s in sides if isinstance(s, ast.Constant)]
        if not any(n.id == "action_name" for n in names):
            continue
        for c in constants:
            if isinstance(c.value, str):
                literals.add(c.value)
    return literals


class DispatchSpecialCaseRatchetTests(unittest.TestCase):

    def test_dispatch_has_no_unsanctioned_action_name_branches(self) -> None:
        fn = _dispatch_function_node()
        found = _string_literals_compared_with_action_name(fn)
        unsanctioned = found - _ALLOWED_ACTION_NAME_LITERALS
        self.assertFalse(
            unsanctioned,
            "Per-action elif branches reintroduced in "
            "_dispatch_action: "
            f"{sorted(unsanctioned)}. Add the new action as a "
            "contract job in contracts/services/<svc>.yaml with a "
            "JobContext adapter; the dispatch should stay a single "
            "run_job() call. If you genuinely need a special case, "
            "add the literal to _ALLOWED_ACTION_NAME_LITERALS in "
            "this test file with a one-line justification.",
        )

    def test_dispatch_calls_run_job(self) -> None:
        """Belt-and-suspenders: the dispatch must actually invoke
        ``run_job`` somewhere — otherwise the special-case check
        would pass against a broken dispatcher."""
        fn = _dispatch_function_node()
        found_run_job = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_job"
            for node in ast.walk(fn)
        )
        self.assertTrue(
            found_run_job,
            "_dispatch_action no longer calls run_job() — the "
            "single-dispatch contract is broken.",
        )

    def test_dispatch_run_job_call_takes_only_action_name(self) -> None:
        """Per-job tuning (max_attempts, retry behaviour) belongs
        in the contract YAML, not in the dispatch. The dispatch's
        ``run_job`` call should take exactly one positional arg
        (``action_name``) and zero keyword args. Anything else is
        a hardcoded knob waiting to be moved to the contract."""
        fn = _dispatch_function_node()
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "run_job"):
                continue
            self.assertEqual(
                len(node.args), 1,
                f"run_job has {len(node.args)} positional args. "
                "Only ``action_name`` belongs here; per-job tuning "
                "goes in contracts/services/*.yaml.",
            )
            self.assertFalse(
                node.keywords,
                "run_job has keyword args ("
                f"{[k.arg for k in node.keywords]}); per-job tuning "
                "(max_attempts, etc.) belongs in the contract YAML, "
                "not the dispatch.",
            )

    def test_dispatch_does_not_call_legacy_action_handlers(self) -> None:
        """Catch the regression where someone re-imports
        ``action_post_setup`` etc. and calls them directly. The
        legacy handlers still exist (CLI uses them) but the HTTP
        dispatch should not."""
        fn = _dispatch_function_node()
        legacy_calls: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id.startswith("action_") and node.func.id != "action_name":
                    legacy_calls.add(node.func.id)
        self.assertFalse(
            legacy_calls,
            f"Dispatch calls legacy action_* handlers directly: "
            f"{sorted(legacy_calls)}. Move the action into a "
            "contract job (with a JobContext adapter that calls "
            "the legacy handler) so it routes through run_job().",
        )


class DispatchUnifiedShapeTests(unittest.TestCase):
    """Smoke tests on the dispatch shape — small enough that the
    function should fit in a screen and have a single happy path."""

    def test_dispatch_function_is_under_50_lines(self) -> None:
        """The dispatch should be terse. If it exceeds 50 lines
        someone has reintroduced complexity that belongs in
        ``run_job`` or in the adapters."""
        fn = _dispatch_function_node()
        end = fn.end_lineno or fn.lineno
        lines = end - fn.lineno + 1
        self.assertLessEqual(
            lines, 50,
            f"_dispatch_action is {lines} lines. Keep the dispatch "
            "small: action-specific work belongs in the job adapter "
            "(services/apps/core/job_adapters.py) or per-app "
            "service module, not in the dispatch.",
        )


if __name__ == "__main__":
    unittest.main()
