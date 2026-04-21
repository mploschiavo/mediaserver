"""Ratchet: every ``/api/...`` path the dashboard fetches must be
served by a route in ``handlers_get.py`` or ``handlers_post.py``.

Bug shape this catches: the dashboard references
``apiFetch('/api/something')``, someone refactors the route on
the server side (renames, moves, deletes), but the dashboard
keeps calling the old path. At runtime the dashboard quietly
falls through to an error handler; the user sees a feature go
dark with no obvious cause.

Catches at test time:

- Typo in the path (``/api/guardrail`` vs ``/api/guardrails``).
- Route removed from the server but the dashboard still calls it.
- Dashboard added for a route that was never shipped.

Tolerates (with justifications):

- Dynamic segments (``/api/users/${id}``) — we match the literal
  prefix + ``/`` + ``any`` as a single route pattern.
- External URLs (anything not starting with ``/api/``).
- Paths that are legitimately 404 in tests (``/healthz`` probes,
  webhook-out paths the controller emits but doesn't serve)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

_DASHBOARD = (
    ROOT / "src" / "media_stack" / "api" / "dashboard.html"
).read_text(encoding="utf-8")
_GET_HANDLER = (
    ROOT / "src" / "media_stack" / "api" / "handlers_get.py"
).read_text(encoding="utf-8")
_POST_HANDLER = (
    ROOT / "src" / "media_stack" / "api" / "handlers_post.py"
).read_text(encoding="utf-8")


# Paths called from the dashboard. Cover both ``apiFetch`` and
# ``fetch`` (some auth-mgmt calls use raw fetch for custom header
# control). Template literals expose dynamic segments as
# ``${...}``; treat those as a wildcard for matching.
_FETCH_CALLS = re.compile(
    r"""(?:apiFetch|fetch)\(
        \s*[`'"]                 # opening quote / template
        (/api/[^`'"]+?)           # the path
        [`'"]""",                 # closing quote / template
    re.VERBOSE,
)


def _collect_dashboard_paths() -> set[str]:
    """Every /api/... path the dashboard fetches."""
    paths: set[str] = set()
    for m in _FETCH_CALLS.finditer(_DASHBOARD):
        raw = m.group(1)
        # Strip trailing query strings.
        if "?" in raw:
            raw = raw.split("?", 1)[0]
        # Replace ${...} template segments with a wildcard token.
        normalised = re.sub(r"\$\{[^}]+\}", "{}", raw)
        paths.add(normalised)
    return paths


# Route declarations from the handler source. The dispatch uses
# ``if path == "/api/x"`` / ``elif path == ...`` / ``path.startswith``,
# plus some path.split("/")[...] + ``== "users"`` patterns.
_STATIC_PATH_EQ = re.compile(r'path\s*==\s*[\'"](/api/[^\'"]+)[\'"]')
_PATH_STARTSWITH = re.compile(r'path\.startswith\(\s*[\'"](/api/[^\'"]+)[\'"]')
# Also: handler.path literal matches (seen in handlers_post.py).
_HANDLER_PATH_EQ = re.compile(
    r'handler\.path\s*==\s*[\'"](/api/[^\'"]+)[\'"]')
_HANDLER_PATH_STARTSWITH = re.compile(
    r'handler\.path\.startswith\(\s*[\'"](/api/[^\'"]+)[\'"]')


def _collect_route_patterns() -> tuple[set[str], set[str]]:
    """Return (static_paths, prefix_paths) from handlers_get +
    handlers_post. Static paths match exact strings; prefix paths
    match anything starting with them."""
    static: set[str] = set()
    prefixes: set[str] = set()
    for src in (_GET_HANDLER, _POST_HANDLER):
        for m in _STATIC_PATH_EQ.finditer(src):
            static.add(m.group(1))
        for m in _HANDLER_PATH_EQ.finditer(src):
            static.add(m.group(1))
        for m in _PATH_STARTSWITH.finditer(src):
            prefixes.add(m.group(1))
        for m in _HANDLER_PATH_STARTSWITH.finditer(src):
            prefixes.add(m.group(1))
    return static, prefixes


def _path_is_served(path: str,
                    static: set[str], prefixes: set[str]) -> bool:
    # Exact match against a static route.
    if path in static:
        return True
    # The {}-wildcard normalisation means a dashboard path
    # ``/api/users/{}/reset-password`` should match a server
    # ``/api/users/`` startswith prefix.
    for prefix in prefixes:
        if path.startswith(prefix):
            return True
    # Allow the exact startswith match (some dashboard calls pass
    # the prefix itself).
    if path in prefixes:
        return True
    # Special-case: dashboard ``/api/services/{}/api-key`` etc.
    # maps to a server check that combines startswith +
    # endswith. Walk known multi-part patterns.
    for pattern in _MULTI_PART_PATTERNS:
        if pattern.match(path):
            return True
    return False


# Dashboard paths that follow a ``startswith + endswith`` pattern
# in the handler dispatch (harder to extract via static regex —
# hand-register the shape). Each one is justified by a comment
# explaining the server-side dispatch it maps to.
_MULTI_PART_PATTERNS = [
    # handlers_get.py has:
    #   path.startswith("/api/services/") and path.endswith("/api-key")
    re.compile(r"^/api/services/[^/]+/api-key$"),
    # handlers_post.py has /api/users/<id>/reset-password (etc.)
    # via parts-split dispatch. These are covered by the
    # /api/users/ prefix.
]


# Paths that are intentionally not served by the controller —
# they're external or handled at a different layer. Each entry
# needs a one-line justification.
_TOLERATED_UNMATCHED = {
    # None currently; add with justification when needed.
}


class DashboardApiRoutesTests(unittest.TestCase):

    def test_every_dashboard_apifetch_path_has_a_server_route(self) -> None:
        static, prefixes = _collect_route_patterns()
        dashboard_paths = _collect_dashboard_paths()
        unmatched: list[str] = []
        for path in sorted(dashboard_paths):
            if path in _TOLERATED_UNMATCHED:
                continue
            if _path_is_served(path, static, prefixes):
                continue
            unmatched.append(path)
        self.assertFalse(
            unmatched,
            "Dashboard fetches paths with no matching server "
            "route:\n  - " + "\n  - ".join(unmatched)
            + "\n\nFix: either add the route in handlers_get.py / "
              "handlers_post.py, or correct the path in "
              "dashboard.html. If the path is served by a "
              "different backend (external proxy, etc.), add it "
              "to _TOLERATED_UNMATCHED with a one-line "
              "justification.",
        )

    def test_collection_not_empty(self) -> None:
        """Belt-and-suspenders: if the regex stops matching, the
        primary test would pass trivially. Pin that the collector
        finds a reasonable number of paths."""
        paths = _collect_dashboard_paths()
        self.assertGreater(
            len(paths), 20,
            f"Only found {len(paths)} dashboard API paths — "
            "the collector regex probably broke after a refactor.",
        )
        static, prefixes = _collect_route_patterns()
        self.assertGreater(
            len(static) + len(prefixes), 20,
            f"Only found {len(static) + len(prefixes)} server "
            "routes — the route-pattern regex likely broke.",
        )


if __name__ == "__main__":
    unittest.main()
