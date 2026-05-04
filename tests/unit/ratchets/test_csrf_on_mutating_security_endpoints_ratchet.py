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

ADR-0007 Phase E retired ``handlers_post.py`` and lifted the CSRF
allowlist into ``services/csrf_exempt_paths.py`` (canonical name:
``CSRF_EXEMPT_POST_PATHS``). The dispatch preflight that calls
``_check_csrf`` lives in ``server.py::_global_post_preflight``.

What the checks do
------------------
1. Parse ``services/csrf_exempt_paths.py`` with ``ast``, locate the
   ``CSRF_EXEMPT_POST_PATHS`` ``frozenset({...})`` literal, and
   assert none of its entries match the security prefixes.
2. Confirm ``server.py`` defines ``_global_post_preflight`` and
   that it calls ``_check_csrf`` (the only path through which
   non-exempt POSTs reach a handler body).

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

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"
CSRF_EXEMPT_PATHS_MODULE = SRC / "api" / "services" / "csrf_exempt_paths.py"
SERVER_MODULE = SRC / "api" / "server.py"


# Security-sensitive URL prefixes. ANY exact-path or startswith match
# against one of these inside ``CSRF_EXEMPT_POST_PATHS`` is a fail.
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


# Paths permitted in ``CSRF_EXEMPT_POST_PATHS``. Each entry names a
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


# Names accepted as the canonical CSRF-exempt set. Both the legacy
# ``_CSRF_EXEMPT_POST_PATHS`` and the post-Phase-E
# ``CSRF_EXEMPT_POST_PATHS`` are honoured so a partial migration
# doesn't silently fail.
_EXEMPT_SET_NAMES: frozenset[str] = frozenset({
    "CSRF_EXEMPT_POST_PATHS",
    "_CSRF_EXEMPT_POST_PATHS",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module_ast() -> ast.Module:
    return ast.parse(CSRF_EXEMPT_PATHS_MODULE.read_text(encoding="utf-8"))


def _extract_exempt_paths(tree: ast.Module) -> frozenset[str]:
    """Find the ``CSRF_EXEMPT_POST_PATHS = frozenset({...})`` literal
    and return its string contents.

    Accepts both bare ``ast.Assign`` (untyped) and ``ast.AnnAssign``
    (annotated, ``CSRF_EXEMPT_POST_PATHS: frozenset[str] = ...``)
    forms; the lifted module uses the annotated form.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            if any(t.id in _EXEMPT_SET_NAMES for t in targets):
                return _extract_frozenset_strings(node.value)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if (isinstance(target, ast.Name)
                    and target.id in _EXEMPT_SET_NAMES
                    and node.value is not None):
                return _extract_frozenset_strings(node.value)
    raise AssertionError(
        "CSRF_EXEMPT_POST_PATHS assignment not found in "
        f"{CSRF_EXEMPT_PATHS_MODULE.relative_to(ROOT)}; the ratchet "
        "can't prove the invariant.",
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


def _server_defines_global_post_preflight() -> bool:
    """Confirm ``server.py`` defines ``_global_post_preflight``.

    ADR-0007 Phase E moved the CSRF preflight from the legacy
    ``PostRequestHandler._global_preflight`` (in handlers_post.py) to
    a module-level function in server.py invoked before Router
    dispatch.
    """
    tree = ast.parse(SERVER_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_global_post_preflight", "_global_preflight",
        }:
            return True
    return False


def _global_preflight_calls_check_csrf() -> bool:
    """Confirm the preflight function in server.py calls ``_check_csrf``.

    This closes the chain: server preflight → ``_check_csrf``, so any
    path not in ``CSRF_EXEMPT_POST_PATHS`` is verified.
    """
    tree = ast.parse(SERVER_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in {"_global_post_preflight", "_global_preflight"}:
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "_check_csrf"):
                return True
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
            "CSRF_EXEMPT_POST_PATHS is empty or missing — ratchet "
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
            "present in CSRF_EXEMPT_POST_PATHS:\n  - "
            + "\n  - ".join(sorted(stale)),
        )

    def test_dispatcher_runs_global_preflight(self) -> None:
        self.assertTrue(
            _server_defines_global_post_preflight(),
            "server.py no longer defines _global_post_preflight. "
            "Without the central preflight, no POST is CSRF-checked "
            "and this ratchet's invariant is meaningless.",
        )

    def test_global_preflight_runs_check_csrf(self) -> None:
        self.assertTrue(
            _global_preflight_calls_check_csrf(),
            "_global_post_preflight no longer calls _check_csrf. "
            "CSRF validation is bypassed for every non-exempt path.",
        )


# ---------------------------------------------------------------------------
# Self-test of the helpers — exercises both compliant and violating
# samples so the ratchet's own branches are covered without relying on
# the production file's shape.
# ---------------------------------------------------------------------------


_COMPLIANT_SAMPLE = """
CSRF_EXEMPT_POST_PATHS: frozenset[str] = frozenset({
    "/webhooks/arr",
    "/api/auth/login",
})
"""


_VIOLATING_SAMPLE = """
CSRF_EXEMPT_POST_PATHS: frozenset[str] = frozenset({
    "/api/bans/add",
    "/api/auth/login",
})
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

    def test_extract_raises_when_missing(self) -> None:
        tree = ast.parse("x = 1\n")
        with self.assertRaises(AssertionError):
            _extract_exempt_paths(tree)


if __name__ == "__main__":
    unittest.main()
