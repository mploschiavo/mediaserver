"""Ratchet: no string-interpolated SQL in ``src/media_stack/``.

Parameterised queries (``cur.execute("... WHERE x = ?", (x,))``) are
the only correct way to pass user-controlled data to SQLite. An
f-string that inlines a variable into the SQL body is a SQL
injection vector the moment that variable grows a user-controlled
path.

The scanner walks every ``Call`` to ``.execute(...)`` /
``.executemany(...)`` / ``.executescript(...)`` and flags any
first-arg whose shape is:

- ``ast.JoinedStr`` with a ``FormattedValue`` whose ``.value`` is
  NOT a literal, a module-level UPPER_SNAKE_CASE constant bound to a
  literal, or a loop variable bound over one (the canonical
  ``for table in _WEBAUTHN_TABLE_CANDIDATES:`` shape in Authelia).
- ``ast.BinOp`` with ``%`` against a non-literal right-hand side.

Policy: real violations are FIXED (rewrite to ``?`` placeholders),
not allowlisted. ``_ALLOWED_VIOLATIONS`` is reserved for exotic
cases (e.g. ALTER TABLE DDL) and must carry a reason.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"


_EXECUTE_METHODS: frozenset[str] = frozenset({
    "execute", "executemany", "executescript",
})


_ALLOWED_VIOLATIONS: frozenset[str] = frozenset({
    # Format: "<relative_path>:<line>:<reason_tag>". Empty today.
    # Fix, don't allowlist.
})


# ---------------------------------------------------------------------------
# Module-level safe-constant detection
# ---------------------------------------------------------------------------


def _is_upper_snake(name: str) -> bool:
    """``_TOTP_TABLE`` / ``TOTP_TABLE`` — leading underscore optional,
    remainder must be uppercase with underscores and digits only."""
    if not name:
        return False
    core = name.lstrip("_")
    if not core:
        return False
    return core.replace("_", "").isalnum() and core == core.upper()


def _collect_module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    """Return ``{name: rhs_expr}`` for every module-level assignment
    ``NAME = <expr>`` where NAME is UPPER_SNAKE_CASE and <expr> is a
    literal (Constant, Tuple/List/Set of Constants)."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if not _is_upper_snake(target.id):
                continue
            rhs = node.value
            if _is_literal_or_literal_container(rhs):
                out[target.id] = rhs
    return out


def _is_literal_or_literal_container(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(
            isinstance(e, ast.Constant) or _is_literal_or_literal_container(e)
            for e in node.elts
        )
    return False


# ---------------------------------------------------------------------------
# Loop-variable safety
# ---------------------------------------------------------------------------


def _collect_safe_loop_vars(
    tree: ast.Module, constants: dict[str, ast.AST],
) -> set[str]:
    """Return the set of loop-variable names bound inside ``for x in
    <SAFE_CONST>:`` loops anywhere in the module.

    Scope tracking is coarse — we bind the name globally across the
    module. In practice a single module re-using a name ``table`` for
    TWO different iterables is a smell worth flagging anyway, so the
    coarseness is fine.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        iterable = node.iter
        if (isinstance(iterable, ast.Name) and iterable.id in constants):
            _add_for_targets(node.target, out)
    return out


def _add_for_targets(node: ast.AST, out: set[str]) -> None:
    """``for (a, b) in ...`` binds both names; handle nested tuples."""
    if isinstance(node, ast.Name):
        out.add(node.id)
        return
    if isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _add_for_targets(elt, out)


# ---------------------------------------------------------------------------
# Scan one file for unsafe SQL calls
# ---------------------------------------------------------------------------


def _is_safe_formatted_value(
    value: ast.AST,
    constants: dict[str, ast.AST],
    safe_loop_vars: set[str],
) -> bool:
    """An ``ast.FormattedValue.value`` is safe if it's a literal, a
    module-level UPPER_SNAKE_CASE constant, or a loop variable bound
    over one."""
    if isinstance(value, ast.Constant):
        return True
    if isinstance(value, ast.Name):
        if value.id in constants:
            return True
        if value.id in safe_loop_vars:
            return True
    return False


def _joinedstr_is_safe(
    node: ast.JoinedStr,
    constants: dict[str, ast.AST],
    safe_loop_vars: set[str],
) -> bool:
    for part in node.values:
        if isinstance(part, ast.Constant):
            continue
        if isinstance(part, ast.FormattedValue):
            if not _is_safe_formatted_value(
                part.value, constants, safe_loop_vars,
            ):
                return False
            continue
        # Unknown node inside JoinedStr — be conservative.
        return False
    return True


def _percent_format_is_safe(node: ast.BinOp) -> bool:
    """``"SELECT ... %s" % x`` — always unsafe unless the right-hand
    side is a constant. Parameterise with ``?`` instead."""
    if not isinstance(node.op, ast.Mod):
        return False
    # LHS must be a string literal for this to even look like an
    # SQL interpolation.
    if not (isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)):
        return False
    # RHS literal? safe. Otherwise unsafe.
    return isinstance(node.right, ast.Constant)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, kind), ...]`` of unsafe SQL calls in
    ``path``. ``kind`` is ``"fstring"`` or ``"percent"``."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    constants = _collect_module_constants(tree)
    safe_loop_vars = _collect_safe_loop_vars(tree, constants)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in _EXECUTE_METHODS:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.JoinedStr):
            if not _joinedstr_is_safe(first, constants, safe_loop_vars):
                violations.append((first.lineno, "fstring"))
        elif isinstance(first, ast.BinOp):
            if not _percent_format_is_safe(first):
                violations.append((first.lineno, "percent"))
    return violations


def _iter_source_files():
    for path in SRC.rglob("*.py"):
        # Don't scan __pycache__ (shouldn't be on disk but be safe).
        if "__pycache__" in path.parts:
            continue
        yield path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class SqliStaticScanRatchet(unittest.TestCase):

    def test_no_unsafe_sql_interpolation(self) -> None:
        actual: set[str] = set()
        for path in _iter_source_files():
            rel = str(path.relative_to(ROOT))
            for lineno, kind in _scan_file(path):
                actual.add(f"{rel}:{lineno}:{kind}")
        unexpected = actual - {
            ":".join(e.split(":")[:3]) for e in _ALLOWED_VIOLATIONS
        } if _ALLOWED_VIOLATIONS else actual
        self.assertFalse(
            unexpected,
            "SQL-injection-shaped interpolation found. FIX by "
            "rewriting to use ``?`` placeholders — do NOT allowlist. "
            "Sites:\n  - " + "\n  - ".join(sorted(unexpected)),
        )

    def test_authelia_session_admin_is_recognised_as_safe(self) -> None:
        """Anchor: (a) Authelia's file passes the scan, and (b) the
        safety detector, fed a synthesized execute-call against that
        module's real constants, also reports safe. The second layer
        prevents a regression where the detector stops recognising
        ``_TOTP_TABLE`` / loop-over-``_WEBAUTHN_TABLE_CANDIDATES``.
        """
        target = SRC / "services" / "apps" / "authelia" / "session_admin.py"
        self.assertTrue(target.is_file(), f"{target} missing")
        self.assertEqual(_scan_file(target), [])
        tree = ast.parse(target.read_text(encoding="utf-8"))
        consts = _collect_module_constants(tree)
        loop_vars = _collect_safe_loop_vars(tree, consts)
        self.assertIn("_TOTP_TABLE", consts)
        self.assertIn("_AUTH_LOG_TABLE", consts)
        self.assertIn("table", loop_vars)
        for synth_src in (
            'con.execute(f"SELECT * FROM {_TOTP_TABLE}", ())',
            'con.execute(f"SELECT * FROM {table}", ())',
        ):
            synth = ast.parse(synth_src).body[0].value
            self.assertTrue(
                _joinedstr_is_safe(synth.args[0], consts, loop_vars),
                f"safety detector rejected: {synth_src}",
            )

    def test_allowlist_entries_still_match(self) -> None:
        """If any allowlist entry exists, make sure it still names a
        real scan hit. Catches an allowlisted file being deleted /
        refactored without pruning the allowlist."""
        if not _ALLOWED_VIOLATIONS:
            return
        live: set[str] = set()
        for path in _iter_source_files():
            rel = str(path.relative_to(ROOT))
            for lineno, kind in _scan_file(path):
                live.add(f"{rel}:{lineno}:{kind}")
        stale: list[str] = []
        for entry in _ALLOWED_VIOLATIONS:
            trimmed = ":".join(entry.split(":")[:3])
            if trimmed not in live:
                stale.append(entry)
        self.assertFalse(
            stale,
            "Stale SQLi allowlist entries:\n  - " + "\n  - ".join(stale),
        )


# ---------------------------------------------------------------------------
# Self-test — the scanner has two branches (safe vs unsafe) per each
# of two interpolation shapes (f-string vs %-format). Cover all four.
# ---------------------------------------------------------------------------


_SAFE_SAMPLE = """
_TOTP_TABLE = "totp_configurations"
_CANDIDATES = ("a", "b")

class R:
    def ok_constant(self, con):
        con.execute(f"SELECT * FROM {_TOTP_TABLE} WHERE u = ?", (u,))

    def ok_loop(self, con):
        for table in _CANDIDATES:
            con.execute(f"SELECT * FROM {table}", ())

    def ok_plain(self, con):
        con.execute("SELECT 1", ())
"""


_UNSAFE_SAMPLE = """
class R:
    def bad_fstring(self, con, table):
        con.execute(f"SELECT * FROM {table}", ())

    def bad_percent(self, con, x):
        con.execute("SELECT %s" % x, ())
"""


def _scan_source(source: str) -> list[tuple[int, str]]:
    """Test helper: scan a string of source instead of a file."""
    tree = ast.parse(source)
    consts = _collect_module_constants(tree)
    loop_vars = _collect_safe_loop_vars(tree, consts)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in _EXECUTE_METHODS:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.JoinedStr):
            if not _joinedstr_is_safe(first, consts, loop_vars):
                out.append((first.lineno, "fstring"))
        elif isinstance(first, ast.BinOp) and not _percent_format_is_safe(first):
            out.append((first.lineno, "percent"))
    return out


class _HelperSelfTest(unittest.TestCase):

    def test_safe_sample_clean(self) -> None:
        self.assertEqual(_scan_source(_SAFE_SAMPLE), [])

    def test_unsafe_sample_flags_both_shapes(self) -> None:
        hits = _scan_source(_UNSAFE_SAMPLE)
        self.assertEqual(
            sorted({kind for _, kind in hits}), ["fstring", "percent"],
        )
        self.assertEqual(len(hits), 2)

    def test_upper_snake_classification(self) -> None:
        for ok in ("FOO", "_FOO_BAR", "FOO_2"):
            self.assertTrue(_is_upper_snake(ok))
        for bad in ("", "_", "fooBar", "foo"):
            self.assertFalse(_is_upper_snake(bad))

    def test_collects_constants_and_loop_vars(self) -> None:
        tree = ast.parse(_SAFE_SAMPLE)
        consts = _collect_module_constants(tree)
        self.assertIn("_TOTP_TABLE", consts)
        self.assertIn("_CANDIDATES", consts)
        self.assertIn("table", _collect_safe_loop_vars(tree, consts))

    def test_percent_format_detection(self) -> None:
        safe = ast.parse("x = 'SELECT %s' % 'literal'").body[0].value
        unsafe = ast.parse("x = 'SELECT %s' % y").body[0].value
        self.assertTrue(_percent_format_is_safe(safe))
        self.assertFalse(_percent_format_is_safe(unsafe))


if __name__ == "__main__":
    unittest.main()
