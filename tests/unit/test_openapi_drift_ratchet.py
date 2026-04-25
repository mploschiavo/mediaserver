"""Ratchet: keep ``src/media_stack/api/openapi.yaml`` honest about the
routes ``handlers_get.py`` / ``handlers_post.py`` actually dispatch.

The OpenAPI spec is hand-maintained — the audit flagged a pattern
where handlers land first and the spec catches up later. Failure modes:
(1) missing docs (integrators can't discover a new route), (2) ghost
docs (stale entries → consumers 404).

This test walks the dispatcher source via ``ast`` (no import required)
and pins three invariants:
- Every route a handler dispatches to (modulo ``_HANDLER_ONLY_ALLOWLIST``)
  appears in the spec.
- Every spec route (modulo ``x-status: planned`` and ``_SPEC_ONLY_ALLOWLIST``)
  has a matching dispatcher branch.
- Helper functions have real work to do — empty results would silently
  degrade the ratchet into a no-op.

``x-status: planned`` lets the session-visibility feature land its
spec first. See ``docs/openapi-regen.md`` for the worked example.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Iterable

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_DIR = _REPO_ROOT / "src" / "media_stack" / "api"
_HANDLERS_GET = _API_DIR / "handlers_get.py"
_HANDLERS_POST = _API_DIR / "handlers_post.py"
_SPEC = _API_DIR / "openapi.yaml"

# --- Allowlists -----------------------------------------------------------
# Every entry needs a one-line reason; no silent opt-outs.

# Handler-dispatched routes that legitimately have no public spec entry.
_HANDLER_ONLY_ALLOWLIST: frozenset[str] = frozenset({
    # These are dispatcher BRANCH prefixes — the handler uses
    # ``path.startswith("/api/sessions/")`` to route into a
    # sub-dispatcher, but the concrete endpoints (``/api/sessions/active``,
    # ``/api/users/{id}/login-history``, ``/api/bans/users``,
    # ``/api/bans/ips``, ``/api/me/sessions``, ``/api/me/tokens``,
    # ``/api/me/mfa-state``, ``/api/me/login-history``,
    # ``/api/security/failed-logins``, ``/api/security/new-locations``,
    # ``/api/security/concurrent``) are each spec'd individually.
    # The prefix itself is an internal routing shape, not a public
    # endpoint — nothing to document.
    "/api/sessions/",
    "/api/bans/",
    "/api/me/",
    "/api/security/",
    # ``/api/``-prefix aliases of spec'd routes. The handlers accept
    # BOTH the un-prefixed and ``/api/``-prefixed form for
    # backward compat with older clients hitting the controller
    # directly (no Envoy prefix-strip). The canonical paths
    # (``/actions/cancel``, ``/webhooks``, ``/webhooks/test``) ARE
    # spec'd; documenting their aliases would just clone three
    # entries with no operational value. Aliases live at:
    #   handlers_post.py:1506 — /api/actions/cancel
    #   handlers_post.py:1538 — /api/webhooks
    #   handlers_post.py:1497 — /api/webhooks/test
    "/api/actions/cancel",
    "/api/webhooks",
    "/api/webhooks/test",
})

# Spec routes that don't show up as exact strings in handlers_*
# dispatchers because they're matched by a dynamic predicate.
_SPEC_ONLY_ALLOWLIST: frozenset[str] = frozenset({
    # Per-media-server alias — dispatched through
    # ``admin_svc.is_media_server_reset_path(path)`` (not a literal
    # string comparison or startswith). The canonical
    # ``POST /api/media-server/reset`` IS dispatched literally and
    # has a spec entry; this is an alternate entry point into the
    # same code path, driven by the service registry.
    "/api/jellyfin/reset",
    # Templated path — handler dispatches via prefix-startswith
    # (``for prefix in ("/api/actions/", "/actions/"): if
    # path.startswith(prefix): action_name = path[len(prefix):]``)
    # at handlers_post.py:1515. The ratchet's AST walk only sees
    # exact-string ``path == "/foo"`` comparisons and won't pick up
    # this pattern. The action name resolves at runtime against
    # ACTION_PRIORITY + the action registry, so there's no finite
    # set of literal paths to enumerate. Cancel is dispatched
    # separately (literal match) and is spec'd; this template is
    # the catch-all.
    "/actions/{name}",
})


# --- Helpers --------------------------------------------------------------


def _is_path_attr(node: ast.AST) -> bool:
    """True if ``node`` is either the local ``path`` name or the
    ``handler.path`` attribute — the two dispatcher idioms."""
    if isinstance(node, ast.Name) and node.id == "path":
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "handler"
    )


def _extract_handler_routes(source: str) -> tuple[set[str], set[str]]:
    """Walk a handler module AST and return ``(exact, prefix)`` sets.

    ``exact``  — from ``path == "..."`` / ``handler.path == "..."``.
    ``prefix`` — from ``path.startswith("...")`` /
                  ``handler.path.startswith("...")``.

    ``endswith`` is NOT followed — those are secondary filters and the
    ``startswith`` value is the one the spec should document.
    """
    tree = ast.parse(source)
    exact: set[str] = set()
    prefix: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and _is_path_attr(node.left):
            for cmp in node.comparators:
                if (isinstance(cmp, ast.Constant)
                        and isinstance(cmp.value, str)
                        and cmp.value.startswith("/")):
                    exact.add(cmp.value)
                # ``handler.path in ("/x", "/y")`` — tuple / list /
                # set literals of string constants. Walk each element.
                if isinstance(cmp, (ast.Tuple, ast.List, ast.Set)):
                    for elt in cmp.elts:
                        if (isinstance(elt, ast.Constant)
                                and isinstance(elt.value, str)
                                and elt.value.startswith("/")):
                            exact.add(elt.value)
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and func.attr == "startswith"
                    and _is_path_attr(func.value)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.startswith("/")):
                prefix.add(node.args[0].value)
    return exact, prefix


_PATH_PLACEHOLDER_RE = re.compile(r"\{[^/]+\}")


def _normalise_spec_path(spec_path: str) -> str:
    """Collapse OpenAPI placeholders to ``*`` so ``/api/users/{id}``
    compares cleanly with a handler prefix ``/api/users/``."""
    return _PATH_PLACEHOLDER_RE.sub("*", spec_path)


def _handler_covers(spec_path: str, exact: Iterable[str],
                    prefix: Iterable[str]) -> bool:
    """True if a handler either compares exactly against ``spec_path``
    or has a ``startswith`` prefix that the spec's template falls
    under."""
    if spec_path in exact:
        return True
    normalised = _normalise_spec_path(spec_path)
    if "*" in normalised:
        head_end = normalised.index("*")
        cut = normalised.rfind("/", 0, head_end) + 1
        candidate_prefix = normalised[:cut]
        for p in prefix:
            if (p == candidate_prefix
                    or p.startswith(candidate_prefix)
                    or candidate_prefix.startswith(p)):
                return True
    else:
        for p in prefix:
            if spec_path.startswith(p):
                return True
    return False


def _spec_covers(handler_path: str, spec_exact: Iterable[str],
                 spec_prefix_roots: Iterable[str]) -> bool:
    """True if a spec entry exactly matches ``handler_path``, or a spec
    entry's placeholder-expanded prefix is an ancestor of it.

    ``startswith("/api/foo?")`` patterns (used by handlers that sniff
    the raw query-string form) are treated as covered by the
    bare-path exact spec entry — ``/api/foo?`` and ``/api/foo`` are
    the same resource from a documentation perspective.
    """
    if handler_path in spec_exact:
        return True
    if handler_path.endswith("?") and handler_path[:-1] in spec_exact:
        return True
    for root in spec_prefix_roots:
        if handler_path == root or handler_path.startswith(root):
            return True
    return False


def _load_spec() -> dict:
    return yaml.safe_load(_SPEC.read_text(encoding="utf-8")) or {}


_HTTP_METHODS = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head",
})


def _spec_route_sets(spec: dict) -> tuple[set[str], set[str], set[str]]:
    """Return ``(exact, prefix_roots, planned)`` route sets.

    ``exact`` — spec paths without any ``{placeholder}``.
    ``prefix_roots`` — the literal prefix up to the first ``{`` for
    every placeholdered spec path (e.g. ``/api/users/`` for
    ``/api/users/{user_id}``).
    ``planned`` — every path where any method is tagged
    ``x-status: planned``. Planned paths are excluded from the
    handler-implementation assertion.
    """
    exact: set[str] = set()
    prefix_roots: set[str] = set()
    planned: set[str] = set()
    for path, operations in (spec.get("paths") or {}).items():
        if not isinstance(operations, dict):
            continue
        if "{" in path:
            prefix_roots.add(path[:path.index("{")])
        else:
            exact.add(path)
        for method_name, op in operations.items():
            if not isinstance(op, dict):
                continue
            if method_name not in _HTTP_METHODS:
                continue
            if str(op.get("x-status", "")).strip().lower() == "planned":
                planned.add(path)
    return exact, prefix_roots, planned


# --- Tests ----------------------------------------------------------------


class HandlerRoutesInSpecTest(unittest.TestCase):
    """Every route the handlers dispatch to appears in the OpenAPI spec
    (or is on the explicit ``_HANDLER_ONLY_ALLOWLIST``)."""

    def test_handler_routes_in_spec(self) -> None:
        spec = _load_spec()
        spec_exact, spec_prefix_roots, _planned = _spec_route_sets(spec)
        get_exact, get_prefix = _extract_handler_routes(
            _HANDLERS_GET.read_text(encoding="utf-8"))
        post_exact, post_prefix = _extract_handler_routes(
            _HANDLERS_POST.read_text(encoding="utf-8"))

        # Sanity: the AST walk must find *something* — empty sets
        # would silently turn the ratchet into a no-op.
        self.assertGreater(
            len(get_exact) + len(get_prefix), 50,
            "handlers_get.py route extraction suspiciously small",
        )
        self.assertGreater(
            len(post_exact) + len(post_prefix), 30,
            "handlers_post.py route extraction suspiciously small",
        )

        all_handler_routes = (
            get_exact | post_exact | get_prefix | post_prefix
        )
        missing: list[str] = []
        for route in sorted(all_handler_routes):
            if route in _HANDLER_ONLY_ALLOWLIST:
                continue
            if _spec_covers(route, spec_exact, spec_prefix_roots):
                continue
            missing.append(route)

        self.assertFalse(
            missing,
            "The following handler-dispatched routes are NOT "
            "documented in openapi.yaml. Either add them to the spec "
            "(preferred) or — if they're internal — add them to "
            "_HANDLER_ONLY_ALLOWLIST with a one-line reason:\n  - "
            + "\n  - ".join(missing),
        )


class SpecRoutesHaveHandlersTest(unittest.TestCase):
    """Every documented route (not tagged ``x-status: planned``) is
    implemented by a dispatcher branch."""

    def test_spec_routes_have_handlers(self) -> None:
        spec = _load_spec()
        get_exact, get_prefix = _extract_handler_routes(
            _HANDLERS_GET.read_text(encoding="utf-8"))
        post_exact, post_prefix = _extract_handler_routes(
            _HANDLERS_POST.read_text(encoding="utf-8"))
        all_exact = get_exact | post_exact
        all_prefix = get_prefix | post_prefix

        self.assertGreater(len(all_exact), 50,
                           "handler exact-route set suspiciously small")

        orphans: list[str] = []
        for path, operations in (spec.get("paths") or {}).items():
            if not isinstance(operations, dict):
                continue
            if path in _SPEC_ONLY_ALLOWLIST:
                continue
            is_planned_path = any(
                isinstance(op, dict)
                and str(op.get("x-status", "")).strip().lower() == "planned"
                for op in operations.values()
            )
            if is_planned_path:
                continue
            if _handler_covers(path, all_exact, all_prefix):
                continue
            orphans.append(path)

        self.assertFalse(
            orphans,
            "The following spec paths have no matching dispatcher "
            "branch in handlers_get.py / handlers_post.py. Either "
            "wire up the handler, mark the operation "
            "`x-status: planned`, or — if it's a composite / dynamic "
            "route — add it to _SPEC_ONLY_ALLOWLIST with a reason:\n"
            "  - " + "\n  - ".join(orphans),
        )


class RatchetHelperSanityTest(unittest.TestCase):
    """Coverage for helper functions — a refactor that silently breaks
    extraction fails loud rather than degrading into a no-op ratchet."""

    def test_is_path_attr_recognises_both_idioms_and_rejects_others(self):
        tree = ast.parse(
            "path == '/x'\nhandler.path == '/y'\nother == '/z'\n")
        cmps = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
        self.assertEqual(len(cmps), 3)
        self.assertTrue(_is_path_attr(cmps[0].left))
        self.assertTrue(_is_path_attr(cmps[1].left))
        self.assertFalse(_is_path_attr(cmps[2].left))

    def test_extract_handler_routes_idioms(self) -> None:
        source = (
            "def f(handler):\n"
            "    path = handler.path\n"
            "    if path == '/api/a':\n"
            "        return\n"
            "    if handler.path == '/api/b':\n"
            "        return\n"
            "    if path.startswith('/api/c/'):\n"
            "        return\n"
            "    if handler.path.startswith('/api/d/'):\n"
            "        return\n"
            "    if path in ('/one', '/two'):\n"
            "        return\n"
            "    if handler.path in ['/three']:\n"
            "        return\n"
        )
        exact, prefix = _extract_handler_routes(source)
        self.assertEqual(
            exact,
            {"/api/a", "/api/b", "/one", "/two", "/three"},
        )
        self.assertEqual(prefix, {"/api/c/", "/api/d/"})

    def test_normalise_and_covers(self) -> None:
        self.assertEqual(
            _normalise_spec_path("/api/users/{user_id}/role"),
            "/api/users/*/role",
        )
        self.assertEqual(
            _normalise_spec_path(
                "/api/users/{user_id}/sessions/{session_id}/revoke"),
            "/api/users/*/sessions/*/revoke",
        )
        self.assertTrue(
            _handler_covers("/api/users", {"/api/users"}, set()))
        self.assertTrue(
            _handler_covers(
                "/api/users/{user_id}", set(), {"/api/users/"}))
        self.assertFalse(
            _handler_covers("/api/unused", set(), {"/api/other/"}))

    def test_spec_covers_exact_prefix_and_query_fallback(self) -> None:
        self.assertTrue(
            _spec_covers("/api/foo/bar", {"/api/x"}, {"/api/foo/"}))
        self.assertFalse(
            _spec_covers("/api/other", {"/api/x"}, {"/api/foo/"}))
        # `startswith("/api/logs?")` falls back to the bare-path
        # spec entry for the same resource.
        self.assertTrue(
            _spec_covers("/api/logs?", {"/api/logs"}, set()))
        self.assertFalse(
            _spec_covers("/api/logs?", {"/other"}, set()))

    def test_spec_route_sets_classifies_planned(self) -> None:
        spec = {
            "paths": {
                "/api/live": {"get": {"summary": "live"}},
                "/api/dreaming": {
                    "get": {"x-status": "planned"},
                },
                "/api/users/{id}": {"get": {"summary": "real"}},
            },
        }
        exact, prefix_roots, planned = _spec_route_sets(spec)
        self.assertIn("/api/live", exact)
        self.assertIn("/api/dreaming", exact)
        self.assertIn("/api/users/", prefix_roots)
        self.assertEqual(planned, {"/api/dreaming"})


if __name__ == "__main__":
    unittest.main()
