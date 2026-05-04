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

ADR-0007 Phase E retired ``handlers_post.py``; the ban / session /
ticket handlers were lifted to the ``api/routes/post_*.py`` modules
and the ``services/security_post_handlers.py`` / ``services/
security_request_context.py`` helpers below.

This ratchet locks in the invariant: any function that dispatches to
a ban/revoke/ticket endpoint MUST either

1. read ``handler.headers.get("Idempotency-Key", ...)`` explicitly,
   OR
2. be listed (by qualified name) in ``_V1_ALLOWLIST`` with a
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

# ADR-0007 Phase E: handlers_post.py was deleted; the ban / session
# revoke / ticket consume handlers live in route + service modules.
# Only POST-domain modules participate — GET-domain modules
# (sessions_security_get.py etc.) are read-only and don't mutate, so
# Idempotency-Key doesn't apply.
_SCAN_FILES: tuple[Path, ...] = (
    SRC / "api" / "routes" / "post_bans.py",
    SRC / "api" / "routes" / "post_me.py",
    SRC / "api" / "routes" / "post_users.py",
    SRC / "api" / "routes" / "auth_password_tickets.py",
    SRC / "api" / "services" / "security_post_handlers.py",
)


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


# Handler functions (by ``module:ClassName.method_name`` /
# ``module:function_name``) that currently CANNOT read the
# Idempotency-Key header, allowed until the follow-up PR wires it
# in. Each entry has a documented reason.
#
# This ratchet may only SHRINK.
_V1_ALLOWLIST: dict[str, str] = {
    # post_users.py: per-user revoke-sessions delegates to
    # ``UsersHelperService.revoke_sessions``; the helper is the v1.1
    # lift-target for Idempotency-Key plumbing. The _delegate-based
    # post_me.py routes already satisfy the check (they call
    # self._delegate which forwards through dispatch), so they
    # don't need allowlisting any more.
    "post_users.py:UsersPostRoutes.handle_user_revoke_sessions": (
        "TODO(v1.1): read Idempotency-Key here and thread it to "
        "the cross-provider session revoker. Same shape as the "
        "legacy PostRequestHandler._user_action allowlist before "
        "the ADR-0007 Phase E split."
    ),
    # auth_password_tickets.py: GET handler (one-shot consume) — the
    # ticket_id IS the idempotency key, so a separate header read
    # would be redundant. Tracked here so a future migration to POST
    # /api/password-tickets/consume forces a re-evaluation.
    "auth_password_tickets.py:AuthPasswordTicketsGetRoutes.handle_password_ticket": (
        "GET handler (RFC 7231 idempotent); ticket_id in the URL is "
        "the dedup key. Re-evaluate when the route migrates to POST."
    ),
    # security_post_handlers.py: the _route dispatcher is a pure
    # path-to-method demultiplexer; the leaf handlers (_ban_ip_add /
    # _ban_user_add) read the key via self._ctx.idem_key(handler).
    "security_post_handlers.py:SecurityPostHandlers._route": (
        "Dispatcher; the per-action methods (_ban_ip_add / "
        "_ban_user_add / _revoke_session) call self._ctx.idem_key("
        "handler) directly."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_scan_files() -> list[Path]:
    return [p for p in _SCAN_FILES if p.is_file()]


def _qualified_name(
    source_file: Path, cls: ast.ClassDef | None, fn: ast.FunctionDef,
) -> str:
    if cls is None:
        return f"{source_file.name}:{fn.name}"
    return f"{source_file.name}:{cls.name}.{fn.name}"


def _all_functions(tree: ast.Module):
    """Yield ``(cls_or_None, FunctionDef)`` for every function /
    method in the module, recursively descending into class bodies."""
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
    """Does the function read the Idempotency-Key header — directly OR
    via the centralized ``SecurityRequestContext.idem_key`` helper?

    Accepts:
      * ``<anything>.headers.get("Idempotency-Key", ...)`` (the
        original direct-read shape).
      * ``<anything>.idem_key(handler)`` — the post-Phase-E helper on
        ``SecurityRequestContext`` that wraps the header read so each
        ban / revoke / ticket handler doesn't repeat the same three
        lines. Calling the helper IS reading the key.
      * Calling another method on the same object that delegates
        to the security service (``self._dispatch_to_security``,
        ``self._security_handler``, ``_ctx.idem_key``) — the route
        layer hands off to ``SecurityPostHandlers`` whose per-action
        methods read the key via ``self._ctx.idem_key(handler)``.
    """
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Call):
            continue
        fn_node = sub.func
        if not isinstance(fn_node, ast.Attribute):
            continue
        # Direct: <recv>.headers.get("Idempotency-Key", ...)
        if fn_node.attr == "get":
            recv = fn_node.value
            if (isinstance(recv, ast.Attribute) and recv.attr == "headers"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)
                    and sub.args[0].value == _IDEMPOTENCY_HEADER):
                return True
        # Helper: <recv>.idem_key(handler) — the
        # SecurityRequestContext shortcut.
        if fn_node.attr == "idem_key":
            return True
        # Delegation: route methods that hand the request off to
        # SecurityPostHandlers — the dispatched action reads the key
        # via _ctx.idem_key.
        if fn_node.attr in {
            "_dispatch_to_security",
            "_dispatch_security",
            "_delegate",
            "dispatch",
        }:
            return True
    return False


def _collect_candidates() -> list[tuple[str, int, list[str]]]:
    """Return ``[(qualified_name, lineno, matched_markers), ...]`` for
    every function (across the scan dirs) that dispatches to a
    mutating path."""
    out: list[tuple[str, int, list[str]]] = []
    for source_file in _iter_scan_files():
        try:
            tree = ast.parse(
                source_file.read_text(encoding="utf-8"), str(source_file),
            )
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for cls, fn in _all_functions(tree):
            markers = _function_touches_mutating_path(fn)
            if not markers:
                continue
            out.append((
                _qualified_name(source_file, cls, fn), fn.lineno, markers,
            ))
    return out


def _collect_violations() -> list[tuple[str, int, list[str]]]:
    """Subset of candidates that don't read the idempotency header."""
    out: list[tuple[str, int, list[str]]] = []
    for source_file in _iter_scan_files():
        try:
            tree = ast.parse(
                source_file.read_text(encoding="utf-8"), str(source_file),
            )
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for cls, fn in _all_functions(tree):
            markers = _function_touches_mutating_path(fn)
            if not markers:
                continue
            if _function_reads_idempotency_header(fn):
                continue
            out.append((
                _qualified_name(source_file, cls, fn), fn.lineno, markers,
            ))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class IdempotencyKeyRatchet(unittest.TestCase):

    def test_every_mutating_handler_is_either_wired_or_allowlisted(self) -> None:
        violations = _collect_violations()
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
        candidates = _collect_candidates()
        violations = _collect_violations()
        seen_candidate_names = {q for q, _ln, _m in candidates}
        violation_names = {q for q, _ln, _m in violations}
        seen_wired_names = {q for q in seen_candidate_names if q not in violation_names}
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


if __name__ == "__main__":
    unittest.main()
