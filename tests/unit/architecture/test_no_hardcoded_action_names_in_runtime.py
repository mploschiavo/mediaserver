"""Architecture ratchet pinning the absence of hardcoded action-name
branching in runtime code (ADR-0009 Phase 6.6).

What this catches
-----------------

The shape:

    if action_name == "bootstrap":         # <- branching on a name
        ...
    elif action_name in ("bootstrap", "reconcile"):
        ...

vs the shape that's fine:

    action_trigger("bootstrap", {})        # <- *dispatch* by name
    run_job("reconcile", source="auto-heal")

Branching on the action name means the dispatcher knows about
specific actions and runs different code for them — exactly the
snowflake pattern Phase 6.4 retired. Calling ``action_trigger`` /
``run_job`` with a literal name is just dispatch; the framework
doesn't branch on what it gets.

Why this exists
---------------

Phase 6.4 deleted six hardcoded ``action_name == "bootstrap"`` /
``action_name in ("bootstrap", "reconcile")`` branches from
``cli/commands/controller_serve.py`` (the post-bootstrap recovery
cascade, the heal-sweep timer, the mark-initial-bootstrap-done
call, the auto-run trigger). The framework now drives those wirings
via the contract-declared ``triggers:`` blocks on
``post-bootstrap-recovery`` / ``heal-sweep`` /
``mark-initial-bootstrap-done`` (see
``contracts/services/core.yaml``). This ratchet pins the absence —
a regression that re-adds an ``action_name == "<name>"`` comparison
fails CI before the bake.

The ratchet's twin (``test_no_action_special_cases.py``) polices
``_dispatch_action`` specifically; this one widens the scope to the
controller-serve loop, the api/services/ surface, and any other
runtime code path the framework owns.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


SCAN_ROOTS: tuple[Path, ...] = (
    ROOT / "src" / "media_stack" / "cli",
    ROOT / "src" / "media_stack" / "api" / "services",
    ROOT / "src" / "media_stack" / "application" / "services",
)


# Identifier names that, when compared against a string literal,
# the ratchet treats as evidence of action-name branching. The
# canonical name is ``action_name`` but legacy paths also bind the
# value as ``action`` / ``name`` in narrow scopes.
_BRANCH_IDENTIFIERS: frozenset[str] = frozenset({
    "action_name",
})


# Job-name literals the ratchet is hardest about — the names that
# defined the original snowflakes. Comparisons against any string
# literal flag, but seeing one of these in a Compare/In is treated
# as a high-priority regression and reported with extra context.
_FLAGGED_LITERALS: frozenset[str] = frozenset({
    "bootstrap",
    "reconcile",
})


# Per-file allowlist for legitimate uses (rare). Each entry must be
# justified — the ratchet's purpose is to make the ALLOWED list the
# only place these comparisons live, not to bless drift.
#
# Format: ``relative-path-from-src``: ``set of function names``
# whose body MAY contain the comparison.
_FILE_FUNCTION_ALLOWLIST: dict[str, frozenset[str]] = {
    # ``_dispatch_action`` aliases ``reconcile`` → ``bootstrap`` so
    # the dashboard's "Reconcile" button reaches the canonical
    # bootstrap tree. The companion ratchet
    # ``test_no_action_special_cases.py`` polices the rest of the
    # function. The Phase 6.6 ratchet stays out of that file's
    # specific function but still catches new branches elsewhere.
    "media_stack/cli/commands/controller_dispatch.py": frozenset({
        "_dispatch_action",
    }),
}


class _Violation:
    __slots__ = ("file", "lineno", "context", "literal")

    def __init__(
        self, file: str, lineno: int, context: str, literal: str,
    ) -> None:
        self.file = file
        self.lineno = lineno
        self.context = context
        self.literal = literal

    def __str__(self) -> str:
        return (
            f"  {self.file}:{self.lineno}  in {self.context}  "
            f"-> branches on action_name == {self.literal!r}"
        )


class _RuntimeBranchScanner:
    """Walks a python source tree and reports
    ``<id> == "<job-name>"`` and ``<id> in (..., "<job-name>")``
    comparisons inside function bodies, where ``<id>`` is in
    ``_BRANCH_IDENTIFIERS``.
    """

    def __init__(self, scan_roots: tuple[Path, ...]) -> None:
        self._scan_roots = scan_roots

    def scan(self) -> list[_Violation]:
        violations: list[_Violation] = []
        for root in self._scan_roots:
            if not root.is_dir():
                continue
            for py in sorted(root.rglob("*.py")):
                if "__pycache__" in str(py):
                    continue
                violations.extend(self._scan_file(py))
        return violations

    def _scan_file(self, py: Path) -> list[_Violation]:
        rel = str(py.relative_to(ROOT / "src"))
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            return []
        allowed_fns = _FILE_FUNCTION_ALLOWLIST.get(rel, frozenset())
        violations: list[_Violation] = []
        for fn in self._walk_functions(tree):
            if fn.name in allowed_fns:
                continue
            for compare in self._walk_compares(fn):
                literal = self._branch_literal(compare)
                if literal is None:
                    continue
                violations.append(
                    _Violation(rel, compare.lineno, fn.name, literal),
                )
        return violations

    @staticmethod
    def _walk_functions(tree: ast.AST) -> list[ast.FunctionDef]:
        return [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
        ]

    @staticmethod
    def _walk_compares(fn: ast.FunctionDef) -> list[ast.Compare]:
        return [
            n for n in ast.walk(fn) if isinstance(n, ast.Compare)
        ]

    @classmethod
    def _branch_literal(
        cls, compare: ast.Compare,
    ) -> str | None:
        """Return the offending string literal if ``compare`` is
        ``action_name == "<lit>"`` or ``action_name in (...)``, else
        ``None``."""
        if not compare.ops:
            return None
        op = compare.ops[0]
        sides = [compare.left, *compare.comparators]
        names = {s.id for s in sides if isinstance(s, ast.Name)}
        if not (names & _BRANCH_IDENTIFIERS):
            return None
        if isinstance(op, ast.Eq):
            for c in sides:
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    return c.value
            return None
        if isinstance(op, ast.In):
            for c in sides:
                lit = cls._tuple_or_set_literal(c)
                if lit is not None:
                    return lit
        return None

    @staticmethod
    def _tuple_or_set_literal(node: ast.AST) -> str | None:
        if isinstance(node, (ast.Tuple, ast.Set, ast.List)):
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    return elt.value
        return None


class NoHardcodedActionNamesRatchet(unittest.TestCase):
    """Pin the post-Phase-6.4 invariant: zero ``action_name == "..."``
    or ``action_name in (..., "...")`` branches in runtime code."""

    def test_zero_branches_on_action_name(self) -> None:
        scanner = _RuntimeBranchScanner(SCAN_ROOTS)
        violations = scanner.scan()
        msg_lines = [
            "ADR-0009 Phase 6.6 regression: branches on action_name",
            "found in runtime code. The framework drives behaviour;",
            "branching on the action name is a snowflake. Move the",
            "post-action work into a contract Job with a ``triggers:``",
            "block — see ADR-0009 for the canonical shape.",
            "",
            "Found:",
        ]
        msg_lines.extend(str(v) for v in violations)
        self.assertEqual(violations, [], "\n".join(msg_lines))

    def test_canonical_legacy_literals_no_longer_match(self) -> None:
        """Belt-and-suspenders: this test is loose-fail on top of the
        primary ratchet. If a future scanner refactor accidentally
        stops detecting ``"bootstrap"`` / ``"reconcile"`` literal
        branching, this runs separately and surfaces the regression
        even if the AST walk stops working."""
        scanner = _RuntimeBranchScanner(SCAN_ROOTS)
        violations = scanner.scan()
        flagged = [
            v for v in violations
            if v.literal in _FLAGGED_LITERALS
        ]
        self.assertEqual(
            flagged, [],
            "Legacy snowflake-literal branches detected:\n"
            + "\n".join(str(v) for v in flagged),
        )


if __name__ == "__main__":
    unittest.main()
