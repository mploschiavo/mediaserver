"""Ratchet: mutating ban/revoke/ticket handlers accept Idempotency-Key.

Why a ratchet
-------------
UIs double-click. Networks retry. Anything that BANS an IP, REVOKES a
session, or CONSUMES a password ticket must be idempotent under
retry — otherwise:

- A ban endpoint double-submit bans two overlapping CIDRs and
  confuses the reconciler.
- A session-revoke double-submit emits two audit entries for one
  user intent, polluting the timeline.
- A password-ticket double-submit races two readers against a
  single-use token, resulting in a confusing "already consumed"
  error even when the first read was from the SAME caller.

The server-side primitive already exists: ``core.auth.users.ban_store``
deduplicates on ``idempotency_key`` at write time, and
``core.time_utils.make_idempotency_key`` documents the canonical
sha256 key shape. What's missing is the handler-layer plumbing that
reads the ``Idempotency-Key`` request header and threads it into the
store.

This ratchet locks in the invariant: any POST handler function that
dispatches to a ban/revoke/ticket endpoint MUST either

1. read ``handler.headers.get("Idempotency-Key", ...)`` explicitly,
   OR
2. be listed (by function name) in ``_V1_ALLOWLIST`` with a
   documented reason — typically a TODO pointing at the ticket.

The allowlist may only SHRINK. Every v1.1 PR that wires up a new
handler removes the corresponding name from the allowlist.

Routes under coverage
---------------------
- ``POST /api/bans/**``
- ``POST /api/sessions/*/revoke``
- ``POST /api/emergency-revoke*``
- ``POST /api/me/revoke-others``
- ``POST /api/password-tickets/*``
- ``POST /api/users/*/revoke-sessions`` (existing; behaves like a
  session revoke)
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"
HANDLERS_POST = SRC / "api" / "handlers_post.py"


# Security-sensitive path fragments. A dispatcher branch that tests
# against any of these lands a request at a mutating endpoint we
# need idempotency for.
_MUTATING_PATH_MARKERS: tuple[str, ...] = (
    "/api/bans",
    "/api/sessions/",
    "/api/emergency-revoke",
    "/api/me/revoke-others",
    "/api/password-tickets/",
    # `revoke-sessions` is a subresource action under /api/users/{id}.
    "revoke-sessions",
)


_IDEMPOTENCY_HEADER = "Idempotency-Key"


# Handler functions (by ``ClassName.method_name`` or bare function
# name) that currently CANNOT read the Idempotency-Key header,
# allowed until the follow-up PR wires it in. Each entry has a
# documented reason.
#
# Format: ``"<qualified_name>": "<reason>"``.
#
# This ratchet may only SHRINK.
_V1_ALLOWLIST: dict[str, str] = {
    # _user_action is the per-user dispatch map that routes
    # ``POST /api/users/{id}/revoke-sessions`` (and related
    # actions) — the only current handler that owns the
    # ``"revoke-sessions"`` marker. The follow-up PR lifts the
    # Idempotency-Key read into _handle_user_mgmt and threads it
    # through _dispatch_user_mgmt → _user_action into the dispatch
    # map. Tracked in docs/roadmap/ban-revoke-idempotency.md.
    "PostRequestHandler._user_action": (
        "TODO(v1.1): read Idempotency-Key here and thread it to "
        "ban_store / session revoker via the dispatch map. "
        "Today the handler is not idempotent under retry; a UI "
        "double-click issues two revoke_sessions audit entries "
        "with identical content."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse() -> ast.Module:
    return ast.parse(HANDLERS_POST.read_text(encoding="utf-8"))


def _qualified_name(cls: ast.ClassDef | None, fn: ast.FunctionDef) -> str:
    if cls is None:
        return fn.name
    return f"{cls.name}.{fn.name}"


def _all_functions(tree: ast.Module):
    """Yield ``(cls_or_None, FunctionDef)`` for every function /
    method in the module."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef):
                    yield node, sub
        elif isinstance(node, ast.FunctionDef):
            yield None, node


def _function_touches_mutating_path(fn: ast.FunctionDef) -> list[str]:
    """Return the list of distinct mutating-path markers that appear
    as string constants anywhere in the function body. Empty list
    means 'this function doesn't own a mutating branch'."""
    found: set[str] = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            for marker in _MUTATING_PATH_MARKERS:
                if marker in sub.value:
                    found.add(marker)
    return sorted(found)


def _function_reads_idempotency_header(fn: ast.FunctionDef) -> bool:
    """Does the function include an ``<anything>.headers.get(
    "Idempotency-Key", ...)`` call?

    Accepts any receiver of ``.headers.get(...)`` so helpers that
    take ``request`` or ``h`` instead of ``handler`` still count.
    """
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Call):
            continue
        fn_node = sub.func
        if not isinstance(fn_node, ast.Attribute) or fn_node.attr != "get":
            continue
        recv = fn_node.value
        if not (isinstance(recv, ast.Attribute) and recv.attr == "headers"):
            continue
        # First positional arg must be the "Idempotency-Key" literal.
        if not sub.args:
            continue
        first = sub.args[0]
        if (isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value == _IDEMPOTENCY_HEADER):
            return True
    return False


def _collect_candidates(tree: ast.Module) -> list[tuple[str, int, list[str]]]:
    """Return ``[(qualified_name, lineno, matched_markers), ...]`` for
    every function that dispatches to a mutating path."""
    out: list[tuple[str, int, list[str]]] = []
    for cls, fn in _all_functions(tree):
        markers = _function_touches_mutating_path(fn)
        if not markers:
            continue
        out.append((_qualified_name(cls, fn), fn.lineno, markers))
    return out


def _collect_violations(tree: ast.Module) -> list[tuple[str, int, list[str]]]:
    """Subset of candidates that don't read the idempotency header."""
    out: list[tuple[str, int, list[str]]] = []
    for cls, fn in _all_functions(tree):
        markers = _function_touches_mutating_path(fn)
        if not markers:
            continue
        if _function_reads_idempotency_header(fn):
            continue
        out.append((_qualified_name(cls, fn), fn.lineno, markers))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class IdempotencyKeyRatchet(unittest.TestCase):

    def test_every_mutating_handler_is_either_wired_or_allowlisted(self) -> None:
        tree = _parse()
        violations = _collect_violations(tree)
        unexpected: list[str] = []
        for qname, lineno, markers in violations:
            if qname in _V1_ALLOWLIST:
                continue
            unexpected.append(
                f"{qname} (line {lineno}; dispatches to {', '.join(markers)})"
            )
        self.assertFalse(
            unexpected,
            "Mutating security handler(s) do not read the "
            "Idempotency-Key header and are not on the v1 allowlist. "
            "Either wire in ``handler.headers.get('Idempotency-Key', "
            "'')`` and thread it to the store, OR add the function "
            "name to _V1_ALLOWLIST with a concrete TODO.\n  - "
            + "\n  - ".join(sorted(unexpected)),
        )

    def test_allowlist_entries_still_apply(self) -> None:
        """Catch stale entries: a function on the allowlist that
        EITHER no longer exists, OR has since been wired and should
        therefore leave the allowlist."""
        tree = _parse()
        seen_candidate_names = {q for q, _ln, _m in _collect_candidates(tree)}
        seen_wired_names = {
            q for q, _ln, _m in _collect_candidates(tree)
            if q not in {v for v, _ln2, _m2 in _collect_violations(tree)}
        }
        stale: list[str] = []
        for name in sorted(_V1_ALLOWLIST):
            if name not in seen_candidate_names:
                stale.append(f"{name} (no longer dispatches to a mutating path)")
            elif name in seen_wired_names:
                stale.append(f"{name} (now reads Idempotency-Key — allowlist entry is dead weight)")
        self.assertFalse(
            stale,
            "Stale _V1_ALLOWLIST entries:\n  - " + "\n  - ".join(stale),
        )

    def test_allowlist_entries_have_reasons(self) -> None:
        """The allowlist MUST carry a non-empty reason per entry."""
        missing = [k for k, v in _V1_ALLOWLIST.items() if not v.strip()]
        self.assertFalse(
            missing,
            "Allowlist entries missing reason text:\n  - "
            + "\n  - ".join(missing),
        )


# ---------------------------------------------------------------------------
# Self-test — exercises the helper's two branches on canned input so
# coverage stays high even when the production file only needs the
# allowlisted branch.
# ---------------------------------------------------------------------------


_COMPLIANT_HANDLER = """
class H:
    def _handle_ban(self, handler):
        key = handler.headers.get("Idempotency-Key", "")
        if handler.path.startswith("/api/bans/"):
            do(ban_store, key)
"""


_VIOLATING_HANDLER = """
class H:
    def _handle_ban(self, handler):
        if handler.path.startswith("/api/bans/"):
            do(ban_store)
"""


_UNRELATED_HANDLER = """
class H:
    def _handle_config(self, handler):
        if handler.path == "/config":
            do(cfg)
"""


class _HelperSelfTest(unittest.TestCase):

    def test_reads_idempotency_header(self) -> None:
        tree = ast.parse(_COMPLIANT_HANDLER)
        for cls, fn in _all_functions(tree):
            if fn.name == "_handle_ban":
                self.assertTrue(_function_reads_idempotency_header(fn))
                self.assertEqual(
                    _function_touches_mutating_path(fn), ["/api/bans"],
                )

    def test_flags_missing_header(self) -> None:
        tree = ast.parse(_VIOLATING_HANDLER)
        found = False
        for cls, fn in _all_functions(tree):
            if fn.name == "_handle_ban":
                self.assertFalse(_function_reads_idempotency_header(fn))
                self.assertEqual(
                    _function_touches_mutating_path(fn), ["/api/bans"],
                )
                found = True
        self.assertTrue(found)

    def test_ignores_unrelated_handler(self) -> None:
        tree = ast.parse(_UNRELATED_HANDLER)
        for cls, fn in _all_functions(tree):
            self.assertEqual(_function_touches_mutating_path(fn), [])

    def test_collect_violations_on_canned_input(self) -> None:
        tree = ast.parse(_VIOLATING_HANDLER + _COMPLIANT_HANDLER.replace(
            "class H", "class G",
        ))
        vios = _collect_violations(tree)
        self.assertEqual(
            [q for q, _, _ in vios], ["H._handle_ban"],
        )


if __name__ == "__main__":
    unittest.main()
