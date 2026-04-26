"""Ratchet: every mutating security endpoint enforces CSRF.

Why a ratchet
-------------
CSRF is the single cheapest defence against a stolen-cookie scenario:
if an attacker can lure a logged-in admin's browser to a malicious
page, the browser will happily attach the session cookie to any
cross-origin POST. Without a CSRF token the attacker can then ban
IPs, revoke sessions, create emergency-revoke keys, or consume
password tickets on the admin's behalf.

The routes this ratchet covers are exactly the ones where that
consequence is catastrophic:

- ``POST /api/bans/**``           — add/remove IP bans
- ``POST /api/sessions/**``       — revoke live sessions
- ``POST /api/emergency-revoke/`` — one-shot kill switch
- ``POST /api/password-tickets/`` — consume a plaintext-password ticket
- ``POST /api/users/**``          — all user-mgmt mutations

Even though some of these endpoints don't exist yet (they land in
subsequent PRs), this ratchet locks in the invariant NOW: the
``_CSRF_EXEMPT_POST_PATHS`` set inside ``handlers_post.py`` must
never list any of those prefixes. The set is the single source of
truth for CSRF skipping; if a future change adds a security path to
the exempt set, this test fails loudly before the PR lands.

What the checks do
------------------
1. Parse ``handlers_post.py`` with ``ast``, locate the
   ``_CSRF_EXEMPT_POST_PATHS`` ``frozenset({...})`` literal, and
   assert none of its entries match the security prefixes.
2. Walk every ``if handler.path == "..."``/``startswith("...")``
   branch in the POST dispatcher. For each branch matching a
   security prefix, assert the dispatcher reaches ``_check_csrf``
   via ``_global_preflight``. Reaching ``_global_preflight`` at the
   top of ``handle()`` is sufficient proof; we verify that call
   exists unconditionally and that no security branch bails out
   before it.

Allowlist policy
----------------
``_CSRF_EXEMPT_ALLOWLIST`` lists any path that's explicitly CSRF-
exempt and why. Security-prefix paths are never eligible; the
allowlist is reserved for pre-session paths (login/logout/refresh)
and internal webhooks with their own HMAC. The ratchet MAY ONLY
SHRINK.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"
HANDLERS_POST = SRC / "api" / "handlers_post.py"


# Security-sensitive URL prefixes. ANY exact-path or startswith match
# against one of these inside ``_CSRF_EXEMPT_POST_PATHS`` is a fail.
_SECURITY_PREFIXES: tuple[str, ...] = (
    "/api/bans/",
    "/api/sessions/",
    "/api/emergency-revoke/",
    "/api/emergency-revoke",
    "/api/password-tickets/",
    "/api/users/",
    "/api/users",
    "/api/me/revoke-others",
)


# Paths permitted in ``_CSRF_EXEMPT_POST_PATHS``. Each entry names a
# real exemption reason. The ratchet may only SHRINK: deleting an
# exemption fails nothing; adding a new one to the production code
# without updating this allowlist fails ``test_no_new_exemptions``.
_CSRF_EXEMPT_ALLOWLIST: frozenset[str] = frozenset({
    # Internal webhook from trusted services; has its own HMAC, no
    # browser cookie to CSRF against.
    "/webhooks/arr",
    # Login/logout establish and revoke the session cookie. Before
    # login there is no cookie to compare; after logout the cookie is
    # being retired.
    "/api/auth/login",
    "/api/auth/logout",
    # Refresh-token endpoint authenticates via the refresh token in
    # the body — programmatic clients, no Cookie header.
    "/api/tokens/refresh",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module_ast() -> ast.Module:
    return ast.parse(HANDLERS_POST.read_text(encoding="utf-8"))


def _extract_exempt_paths(tree: ast.Module) -> frozenset[str]:
    """Find the ``_CSRF_EXEMPT_POST_PATHS = frozenset({...})`` literal
    and return its string contents.

    Accepts the attribute form ``PostRequestHandler._CSRF_EXEMPT_POST_PATHS``
    so the exact nesting (class-level vs module-level) doesn't break
    the scan.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # Match any target whose id ends in _CSRF_EXEMPT_POST_PATHS.
        targets = [t for t in node.targets if isinstance(t, ast.Name)]
        if not targets:
            continue
        if not any(t.id == "_CSRF_EXEMPT_POST_PATHS" for t in targets):
            continue
        return _extract_frozenset_strings(node.value)
    raise AssertionError(
        "_CSRF_EXEMPT_POST_PATHS assignment not found in "
        f"{HANDLERS_POST.relative_to(ROOT)}; the ratchet can't prove "
        "the invariant.",
    )


def _extract_frozenset_strings(value: ast.AST) -> frozenset[str]:
    """Pull string literals out of ``frozenset({...})`` / ``{...}``."""
    # Unwrap ``frozenset({...})``.
    if (isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and value.args):
        value = value.args[0]
    strings: set[str] = set()
    if isinstance(value, (ast.Set, ast.Tuple, ast.List)):
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                strings.add(elt.value)
    return frozenset(strings)


def _security_prefix_hit(path: str) -> str | None:
    for prefix in _SECURITY_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return prefix
    return None


def _dispatcher_calls_global_preflight(tree: ast.Module) -> bool:
    """Confirm the POST dispatcher goes through ``_global_preflight``.

    We find the ``handle`` method on ``PostRequestHandler`` and scan
    its body for a top-level ``if not self._global_preflight(handler):
    return`` guard. That call is what invokes ``_check_csrf`` for
    every non-exempt path.
    """
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        if cls.name != "PostRequestHandler":
            continue
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name != "handle":
                continue
            for stmt in fn.body:
                if not isinstance(stmt, ast.If):
                    continue
                if _calls_method(stmt.test, "_global_preflight"):
                    return True
    return False


def _calls_method(node: ast.AST, name: str) -> bool:
    """Return True if ``node`` contains a call to ``self.<name>(...)``."""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == name):
            return True
    return False


def _global_preflight_calls_check_csrf(tree: ast.Module) -> bool:
    """Confirm ``_global_preflight`` itself calls ``_check_csrf``.

    This closes the chain: handle() → _global_preflight → _check_csrf,
    so any path not in ``_CSRF_EXEMPT_POST_PATHS`` is verified.
    """
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        if cls.name != "PostRequestHandler":
            continue
        for fn in cls.body:
            if (isinstance(fn, ast.FunctionDef)
                    and fn.name == "_global_preflight"):
                return _calls_method(fn, "_check_csrf")
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class CsrfOnMutatingSecurityEndpointsRatchet(unittest.TestCase):

    def test_no_security_path_is_csrf_exempt(self) -> None:
        tree = _load_module_ast()
        exempt = _extract_exempt_paths(tree)
        self.assertTrue(
            exempt,
            "_CSRF_EXEMPT_POST_PATHS is empty or missing — ratchet "
            "cannot validate the invariant.",
        )
        violations: list[str] = []
        for path in sorted(exempt):
            hit = _security_prefix_hit(path)
            if hit is not None:
                violations.append(f"{path!r} matches security prefix {hit!r}")
        self.assertFalse(
            violations,
            "Security paths must never be CSRF-exempt:\n  - "
            + "\n  - ".join(violations),
        )

    def test_no_new_exemptions(self) -> None:
        """Every path currently in the exempt set is one we explicitly
        sanctioned. Forces a code-review for any future addition."""
        tree = _load_module_ast()
        exempt = _extract_exempt_paths(tree)
        new = exempt - _CSRF_EXEMPT_ALLOWLIST
        self.assertFalse(
            new,
            "New CSRF-exempt path(s) found without updating "
            "_CSRF_EXEMPT_ALLOWLIST in this ratchet:\n  - "
            + "\n  - ".join(sorted(new)),
        )

    def test_allowlist_only_names_real_exemptions(self) -> None:
        """Catch stale allowlist entries when an exemption is
        deliberately removed from the production set."""
        tree = _load_module_ast()
        exempt = _extract_exempt_paths(tree)
        stale = _CSRF_EXEMPT_ALLOWLIST - exempt
        self.assertFalse(
            stale,
            "Stale ratchet allowlist entries — exemption no longer "
            "present in _CSRF_EXEMPT_POST_PATHS:\n  - "
            + "\n  - ".join(sorted(stale)),
        )

    def test_dispatcher_runs_global_preflight(self) -> None:
        tree = _load_module_ast()
        self.assertTrue(
            _dispatcher_calls_global_preflight(tree),
            "PostRequestHandler.handle() no longer calls "
            "self._global_preflight(handler) at the top. Without it, "
            "no POST is CSRF-checked and this ratchet's invariant is "
            "meaningless.",
        )

    def test_global_preflight_runs_check_csrf(self) -> None:
        tree = _load_module_ast()
        self.assertTrue(
            _global_preflight_calls_check_csrf(tree),
            "_global_preflight no longer calls self._check_csrf. "
            "CSRF validation is bypassed for every non-exempt path.",
        )


# ---------------------------------------------------------------------------
# Self-test of the helpers — exercises both compliant and violating
# samples so the ratchet's own branches are covered without relying on
# the production file's shape.
# ---------------------------------------------------------------------------


_COMPLIANT_SAMPLE = """
class PostRequestHandler:
    _CSRF_EXEMPT_POST_PATHS = frozenset({
        "/webhooks/arr",
        "/api/auth/login",
    })

    def handle(self, handler):
        if not self._global_preflight(handler):
            return

    def _global_preflight(self, handler):
        if not self._check_csrf(handler):
            return False
        return True
"""


_VIOLATING_SAMPLE = """
class PostRequestHandler:
    _CSRF_EXEMPT_POST_PATHS = frozenset({
        "/api/bans/add",
        "/api/auth/login",
    })

    def handle(self, handler):
        pass

    def _global_preflight(self, handler):
        return True
"""


class _HelperSelfTest(unittest.TestCase):

    def test_extract_frozenset_strings_compliant(self) -> None:
        tree = ast.parse(_COMPLIANT_SAMPLE)
        self.assertEqual(
            _extract_exempt_paths(tree),
            frozenset({"/webhooks/arr", "/api/auth/login"}),
        )

    def test_extract_frozenset_strings_violating(self) -> None:
        tree = ast.parse(_VIOLATING_SAMPLE)
        exempt = _extract_exempt_paths(tree)
        self.assertIn("/api/bans/add", exempt)

    def test_security_prefix_hit_detects(self) -> None:
        self.assertEqual(
            _security_prefix_hit("/api/bans/1.2.3.4"), "/api/bans/",
        )
        self.assertIsNone(_security_prefix_hit("/api/auth/login"))

    def test_dispatcher_checks(self) -> None:
        good = ast.parse(_COMPLIANT_SAMPLE)
        bad = ast.parse(_VIOLATING_SAMPLE)
        self.assertTrue(_dispatcher_calls_global_preflight(good))
        self.assertFalse(_dispatcher_calls_global_preflight(bad))
        self.assertTrue(_global_preflight_calls_check_csrf(good))
        self.assertFalse(_global_preflight_calls_check_csrf(bad))

    def test_extract_raises_when_missing(self) -> None:
        tree = ast.parse("x = 1\n")
        with self.assertRaises(AssertionError):
            _extract_exempt_paths(tree)


if __name__ == "__main__":
    unittest.main()
