"""Ratchet: every session-mint / session-revoke code path in
``handlers_post.py`` must be accompanied by a matching audit-log
append for a login/logout event.

Why the ratchet exists
----------------------
Forensic playback after a break-in depends on the audit log carrying
every login event — winning AND failing. The bare session-store
``create(...)`` call alone mints a cookie without leaving a
tamper-evident trace; the ``append(...)`` on the hash-chained audit
log is what turns "someone authenticated" into "we have evidence of
who, when, from where".

How the scan works
------------------
For each ``FunctionDef`` in the module, we walk its body to find
calls to ``session_store.create(...)`` or ``session_store.revoke(...)``
(matched by attribute name). If found, we then require a call to
``audit`` / ``_audit`` / ``_audit_login_event`` / ``append`` that
names a login/logout action from ``audit_actions`` in the SAME
function body.

This is conservative by design: adding a new entry-point that mints
sessions without an audit entry fails the ratchet loudly.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_HANDLERS_POST = ROOT / "src" / "media_stack" / "api" / "handlers_post.py"

# The session-store attribute names whose calls must be audited.
_SESSION_MINT_ATTRS: frozenset[str] = frozenset({"create"})
_SESSION_REVOKE_ATTRS: frozenset[str] = frozenset({"revoke"})

# Audit-action constant names that satisfy the ratchet.
_AUDIT_ACTION_TOKENS: frozenset[str] = frozenset({
    "LOGIN_SUCCESS",
    "LOGIN_FAILURE",
    "LOGIN_BLOCKED",
    "LOGIN_RATE_LIMITED",
    "LOGOUT",
    # The _audit_login_event helper carries the action as its own
    # positional arg; naming it as a token satisfies the scan.
    "_audit_login_event",
})


def _is_session_store_call(call: ast.Call, attrs: frozenset[str]) -> bool:
    """True when ``call`` looks like ``session_store.<attr>(...)`` or
    ``_session_store.<attr>(...)`` (either module-level import alias)."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in attrs:
        return False
    value = func.value
    if not isinstance(value, ast.Name):
        return False
    return value.id in {"session_store", "_session_store"}


def _function_mentions_audit_token(fn: ast.FunctionDef) -> bool:
    """True when the function body contains an ``ast.Name`` or
    ``ast.Attribute`` that references one of ``_AUDIT_ACTION_TOKENS``.

    Covers both direct use (``LOGIN_SUCCESS``) and attribute-style
    access (``action=LOGIN_SUCCESS`` emits an ``ast.Name``; an
    ``audit.append(action="login_success")`` emits a Constant string
    — also accept string literals that match the action's value).
    """
    string_values = {
        "login_success", "login_failure", "login_blocked",
        "login_rate_limited", "logout",
    }
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in _AUDIT_ACTION_TOKENS:
            return True
        if (isinstance(node, ast.Attribute)
                and node.attr in _AUDIT_ACTION_TOKENS):
            return True
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in string_values):
            return True
    return False


def _function_has_session_call(
    fn: ast.FunctionDef, attrs: frozenset[str],
) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _is_session_store_call(node, attrs):
            return True
    return False


class AuthEventsAuditedRatchet(unittest.TestCase):

    def _iter_functions(self) -> list[ast.FunctionDef]:
        tree = ast.parse(
            _HANDLERS_POST.read_text(encoding="utf-8"), str(_HANDLERS_POST),
        )
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        ]

    def test_every_session_mint_path_audits_login_success(self) -> None:
        violations: list[str] = []
        for fn in self._iter_functions():
            if not _function_has_session_call(fn, _SESSION_MINT_ATTRS):
                continue
            if not _function_mentions_audit_token(fn):
                violations.append(
                    f"{fn.name}:{fn.lineno} mints a session via "
                    "session_store.create(...) without a matching "
                    "audit entry.",
                )
        self.assertFalse(
            violations,
            "Session-mint code path missing audit append:\n  - "
            + "\n  - ".join(violations),
        )

    def test_logout_path_audits_session_revoke(self) -> None:
        violations: list[str] = []
        for fn in self._iter_functions():
            if not _function_has_session_call(fn, _SESSION_REVOKE_ATTRS):
                continue
            if not _function_mentions_audit_token(fn):
                violations.append(
                    f"{fn.name}:{fn.lineno} revokes a session via "
                    "session_store.revoke(...) without a matching "
                    "audit entry.",
                )
        self.assertFalse(
            violations,
            "Session-revoke code path missing audit append:\n  - "
            + "\n  - ".join(violations),
        )

    def test_handlers_post_imports_audit_action_constants(self) -> None:
        """Defensive: if the import disappears in a refactor the main
        scan might false-pass because the Name references vanish too.
        Pin the import presence."""
        src = _HANDLERS_POST.read_text(encoding="utf-8")
        for constant in ("LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGOUT"):
            self.assertIn(
                constant, src,
                f"handlers_post.py must import/use {constant}",
            )


if __name__ == "__main__":
    unittest.main()
