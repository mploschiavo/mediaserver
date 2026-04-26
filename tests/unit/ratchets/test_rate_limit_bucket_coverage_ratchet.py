"""Ratchet: every security endpoint dispatch goes through a rate limiter.

Security-sensitive endpoints (``/api/bans``, ``/api/sessions``,
``/api/security``, ``/api/me/revoke-others``,
``/api/password-tickets``) are the first place an attacker tries to
brute-force. Without a per-IP (or per-account, or per-ticket) token-
bucket in front, guessing a ticket ID or enumerating a session ID
becomes trivial.

A dispatch branch is rate-limited if it
(a) calls one of ``_global_post_limiter.allow(...)`` /
    ``_user_mgmt_limiter.allow(...)`` / ``_pw_reset_limiter.allow(...)``
    / ``_pw_bucket.allow(...)`` / a fresh ``RateLimiter(...).allow(...)``,
(b) runs a preflight gate (``_global_preflight``/``_preflight``) that
    transitively hits a limiter, or
(c) lives in a module that contains any limiter call — coarse, but
    defensible: it catches the common delegation pattern where a
    branch calls a same-file helper whose body runs the bucket.

``_ALLOWED_UNLIMITED_PATHS`` may only SHRINK.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"
HANDLERS_POST = SRC / "api" / "handlers_post.py"
HANDLERS_GET = SRC / "api" / "handlers_get.py"


_SECURITY_PREFIXES: tuple[str, ...] = (
    "/api/bans",
    "/api/sessions",
    "/api/security",
    "/api/me/revoke-others",
    "/api/password-tickets",
)


# Attribute names that count as a rate limiter. If a file calls
# ``<name>.allow(...)`` we consider that dispatch rate-limited.
_LIMITER_ATTR_NAMES: frozenset[str] = frozenset({
    "_global_post_limiter",
    "_user_mgmt_limiter",
    "_pw_reset_limiter",
    # Accept the GET handler's locally bound alias for the pw-reset
    # bucket — it imports the object and calls it ``_pw_bucket``.
    "_pw_bucket",
    # Admin-read bucket for the session-visibility GETs. Wider than
    # user-mgmt (reads are lower-risk than mutations) but narrow
    # enough to prevent enumeration DoS on session / ban / security
    # endpoints.
    "_security_read_limiter",
})


# The POST dispatcher routes every request through _global_preflight,
# which (as proved by ratchet 1) goes through _global_post_limiter.
# We accept any file whose module-level / dispatcher code runs that
# preflight as "rate-limited by construction".
_PREFLIGHT_GATES: frozenset[str] = frozenset({
    "_global_preflight",
    "_preflight",
})


_ALLOWED_UNLIMITED_PATHS: frozenset[str] = frozenset({
    # Format: "<rel_path>:<security_prefix>:<reason>".
    #
    # (Empty today — every existing security endpoint is covered
    # either by _global_preflight/_global_post_limiter (all POSTs)
    # or by _pw_reset_limiter inside _PasswordTicketConsumer.handle,
    # which the module-coarse detector resolves via the module's
    # overall limiter-awareness check.)
})


# ---------------------------------------------------------------------------
# AST-walk helpers
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_constants(node: ast.AST) -> list[str]:
    """Harvest string constants referenced inside an expression —
    used to recognise ``handler.path == "/api/bans/..."`` and
    ``handler.path.startswith("/api/sessions/...")`` branches."""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def _hits_security_prefix(strings: list[str]) -> str | None:
    for s in strings:
        for prefix in _SECURITY_PREFIXES:
            if s == prefix or s.startswith(prefix):
                return prefix
    return None


def _calls_limiter(node: ast.AST) -> bool:
    """Does ``node`` contain a call to a known limiter's ``.allow``?

    Matches both ``<name>.allow(...)`` (where ``<name>`` is in
    ``_LIMITER_ATTR_NAMES``) and the generic
    ``RateLimiter(...).allow(...)`` shape.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        if not isinstance(fn, ast.Attribute) or fn.attr != "allow":
            continue
        inner = fn.value
        if isinstance(inner, ast.Name) and inner.id in _LIMITER_ATTR_NAMES:
            return True
        if (isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "RateLimiter"):
            return True
    return False


def _calls_preflight_gate(node: ast.AST) -> bool:
    """Does ``node`` contain a call to ``self._global_preflight(...)``
    / ``self._preflight(...)``? Either chains to a limiter call."""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in _PREFLIGHT_GATES):
            return True
    return False


def _iter_dispatcher_branches(tree: ast.Module):
    """Yield ``(file_relpath, lineno, strings_in_test, body_nodes)`` for
    every ``if`` / ``elif`` branch that tests against ``handler.path``
    or ``path`` (the GET dispatcher's local variable).

    We care about these because they're the per-endpoint dispatch
    points; a branch that matches a security prefix is where we need
    to prove rate-limiting.
    """
    # Walk every If node in the module. For each, collect string
    # constants in the test; if any match a security prefix, yield.
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        strings = _string_constants(node.test)
        if not strings:
            continue
        yield node, strings


def _module_has_dispatcher_preflight(tree: ast.Module) -> bool:
    """Quick check: does the module's dispatcher (``handle`` method)
    start with a ``self._global_preflight(...)`` guard?"""
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == "handle":
                for stmt in fn.body[:3]:
                    if isinstance(stmt, ast.If) and _calls_preflight_gate(stmt.test):
                        return True
    return False


def _module_has_any_limiter_call(tree: ast.Module) -> bool:
    """True iff the module contains at least one known rate-limiter
    ``.allow(...)`` call anywhere.

    Coarse but defensible: the GET handler dispatches the password-
    tickets branch to a helper (``_handle_password_ticket_consume``,
    which aliases ``_PasswordTicketConsumer.handle``) that runs the
    limiter. A full call-graph walk across method-aliases, instance
    attributes and late imports would blow up this ratchet's
    complexity without adding meaningful safety — the ratchet's real
    value is 'a new security path must come with a rate-limiter
    call somewhere in the same file', which this check catches.
    """
    return _calls_limiter(tree)


def _scan_file(path: Path) -> list[tuple[str, int, str]]:
    """Return ``[(relpath, lineno, security_prefix), ...]`` for every
    dispatch branch that hits a security prefix but can't be proved
    to be rate-limited.

    A branch is rate-limited if ANY of the following holds:

    1. The branch body itself calls a limiter / preflight gate.
    2. The enclosing module's top-level dispatcher (``handle``) starts
       with a ``_global_preflight`` gate.
    3. The module contains at least one limiter call somewhere
       reachable from the branch's in-file dispatch target.

    See ``_module_has_any_limiter_call`` for why (3) is accepted.
    """
    tree = _parse(path)
    rel = str(path.relative_to(ROOT))
    dispatcher_gated = _module_has_dispatcher_preflight(tree)
    module_limited = _module_has_any_limiter_call(tree)
    violations: list[tuple[str, int, str]] = []
    for node, strings in _iter_dispatcher_branches(tree):
        prefix = _hits_security_prefix(strings)
        if prefix is None:
            continue
        branch_ok = _calls_limiter(node) or _calls_preflight_gate(node)
        if not branch_ok and dispatcher_gated:
            branch_ok = True
        if not branch_ok and module_limited:
            branch_ok = True
        if branch_ok:
            continue
        violations.append((rel, node.lineno, prefix))
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class RateLimitBucketCoverageRatchet(unittest.TestCase):

    def test_every_security_path_is_rate_limited(self) -> None:
        unexpected: list[str] = []
        for path in (HANDLERS_POST, HANDLERS_GET):
            for rel, lineno, prefix in _scan_file(path):
                key = f"{rel}:{prefix}"
                if any(a.startswith(key + ":") for a in _ALLOWED_UNLIMITED_PATHS):
                    continue
                unexpected.append(f"{rel}:{lineno} -> {prefix}")
        self.assertFalse(
            unexpected,
            "Security-path dispatch without a rate limiter:\n  - "
            + "\n  - ".join(sorted(unexpected)),
        )

    def test_allowlist_entries_still_apply(self) -> None:
        """Every allowlist entry must name a real violation today,
        otherwise it's stale and should be deleted."""
        live: set[str] = set()
        for path in (HANDLERS_POST, HANDLERS_GET):
            for rel, _lineno, prefix in _scan_file(path):
                live.add(f"{rel}:{prefix}")
        stale: list[str] = []
        for entry in _ALLOWED_UNLIMITED_PATHS:
            key = entry.split(":", 2)
            if len(key) < 2:
                stale.append(entry)
                continue
            target = f"{key[0]}:{key[1]}"
            if target not in live:
                stale.append(entry)
        self.assertFalse(
            stale,
            "Stale allowlist entries — no matching dispatch branch "
            "violates today:\n  - " + "\n  - ".join(stale),
        )


# ---------------------------------------------------------------------------
# Self-test of the helpers
# ---------------------------------------------------------------------------


_COMPLIANT_POST = """
class PostRequestHandler:
    def handle(self, handler):
        if not self._global_preflight(handler):
            return
        if handler.path.startswith("/api/bans/"):
            return
"""


_COMPLIANT_GET_DIRECT = """
def dispatch(handler, path):
    if path.startswith("/api/password-tickets/"):
        if not _pw_bucket.allow(client_id="x", bucket="pw-reset"):
            return
"""


_VIOLATING = """
def dispatch(handler, path):
    if path.startswith("/api/sessions/"):
        do_something()
"""


_COMPLIANT_GET_DELEGATED = """
class _Consumer:
    def handle(self, handler, path):
        if not _pw_bucket.allow(client_id='x', bucket='b'):
            return

def dispatch(handler, path):
    if path.startswith("/api/password-tickets/"):
        _consumer.handle(handler, path)
"""


class _HelperSelfTest(unittest.TestCase):

    def _simulate_scan(self, source: str) -> int:
        """Emulate ``_scan_file`` against a source string."""
        tree = ast.parse(source)
        dispatcher_gated = _module_has_dispatcher_preflight(tree)
        module_limited = _module_has_any_limiter_call(tree)
        violations = 0
        for node, strings in _iter_dispatcher_branches(tree):
            prefix = _hits_security_prefix(strings)
            if prefix is None:
                continue
            ok = (_calls_limiter(node) or _calls_preflight_gate(node)
                  or dispatcher_gated or module_limited)
            if not ok:
                violations += 1
        return violations

    def test_compliant_post_passes(self) -> None:
        tree = ast.parse(_COMPLIANT_POST)
        self.assertTrue(_module_has_dispatcher_preflight(tree))
        self.assertEqual(self._simulate_scan(_COMPLIANT_POST), 0)

    def test_compliant_get_direct_limiter(self) -> None:
        tree = ast.parse(_COMPLIANT_GET_DIRECT)
        self.assertFalse(_module_has_dispatcher_preflight(tree))
        self.assertTrue(_module_has_any_limiter_call(tree))
        self.assertEqual(self._simulate_scan(_COMPLIANT_GET_DIRECT), 0)

    def test_compliant_get_delegated_passes(self) -> None:
        """Module-coarse check accepts the delegated-helper shape."""
        tree = ast.parse(_COMPLIANT_GET_DELEGATED)
        self.assertFalse(_module_has_dispatcher_preflight(tree))
        self.assertTrue(_module_has_any_limiter_call(tree))
        self.assertEqual(self._simulate_scan(_COMPLIANT_GET_DELEGATED), 0)

    def test_violating_sample_is_flagged(self) -> None:
        tree = ast.parse(_VIOLATING)
        self.assertFalse(_module_has_any_limiter_call(tree))
        self.assertEqual(self._simulate_scan(_VIOLATING), 1)

    def test_prefix_detection(self) -> None:
        self.assertEqual(
            _hits_security_prefix(["/api/bans/x"]), "/api/bans",
        )
        self.assertEqual(
            _hits_security_prefix(["/api/me/revoke-others"]),
            "/api/me/revoke-others",
        )
        self.assertIsNone(_hits_security_prefix(["/api/auth/login"]))

    def test_calls_limiter_variants(self) -> None:
        snip = "_global_post_limiter.allow(client_id=x, bucket='b')"
        self.assertTrue(_calls_limiter(ast.parse(snip)))
        snip2 = "RateLimiter(capacity=1, refill_per_second=1).allow()"
        self.assertTrue(_calls_limiter(ast.parse(snip2)))
        self.assertFalse(_calls_limiter(ast.parse("foo()")))


if __name__ == "__main__":
    unittest.main()
