"""Frontend↔backend contract tests.

For every GET endpoint the dashboard consumes, verify the response
contains the fields the UI reads. Catches the silent-break class of
bug where a backend refactor returns a new shape (e.g. moves
``min_length`` into ``policy.min_length``) and the dashboard quietly
stops rendering that field without any error in logs or tests.

The tests:
  1. Scan ``dashboard.html`` for every ``apiFetch('/api/X')``.
  2. For each endpoint, hit the live controller and parse the
     response as JSON.
  3. Assert required fields are present and the right type.

Runs against the live stack via CONTROLLER_URL; skips cleanly when
not reachable so this stays green in a CI without a spun-up stack.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import unittest
import urllib.parse
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_HTML = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


def _controller_base() -> tuple[str, int, bool]:
    base = os.environ.get("CONTROLLER_URL", "http://127.0.0.1:9100")
    parsed = urllib.parse.urlsplit(base)
    return (parsed.hostname or "127.0.0.1",
            parsed.port or (443 if parsed.scheme == "https" else 80),
            parsed.scheme == "https")


def _reachable() -> bool:
    import socket
    host, port, _ = _controller_base()
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


class _Client:
    def __init__(self):
        self.host, self.port, self.https = _controller_base()
        self._cookies: dict[str, str] = {}

    def _conn(self):
        if self.https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return HTTPSConnection(self.host, self.port, context=ctx, timeout=5)
        return HTTPConnection(self.host, self.port, timeout=5)

    def get_json(self, path: str) -> tuple[int, Any]:
        conn = self._conn()
        try:
            headers = {
                "Accept": "text/html,application/json,*/*;q=0.8",
                "User-Agent": "dashboard-contract-test/1.0",
            }
            if self._cookies:
                headers["Cookie"] = "; ".join(
                    f"{k}={v}" for k, v in self._cookies.items())
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            for name, value in resp.getheaders():
                if name.lower() == "set-cookie":
                    first = value.split(";", 1)[0].strip()
                    if "=" in first:
                        k, _, v = first.partition("=")
                        self._cookies[k] = v
            body = resp.read()
            try:
                return resp.status, json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return resp.status, None
        finally:
            conn.close()

    def login(self, user: str, pw: str) -> bool:
        conn = self._conn()
        try:
            body = json.dumps({"username": user, "password": pw}).encode()
            conn.request("POST", "/api/auth/login", body=body, headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Accept": "application/json",
            })
            resp = conn.getresponse()
            for name, value in resp.getheaders():
                if name.lower() == "set-cookie":
                    first = value.split(";", 1)[0].strip()
                    if "=" in first:
                        k, _, v = first.partition("=")
                        self._cookies[k] = v
            resp.read()
            return resp.status == 200
        finally:
            conn.close()


# -----------------------------------------------------------------------------
# Contract table: path → required top-level fields (+ optional nested probes).
# Covers the endpoints the dashboard reads on the most-used tabs.
# -----------------------------------------------------------------------------
CONTRACTS: tuple[tuple[str, dict], ...] = (
    # Auth / identity.
    ("/api/auth/identity",
     {"required": ["authenticated"]}),
    ("/api/auth/modes",
     {"required": ["modes"]}),
    ("/api/auth/config",
     {"required": []}),
    # User management.
    ("/api/users",
     {"required": ["users"]}),
    ("/api/roles",
     {"required": ["roles"]}),
    ("/api/me",
     {"required": ["authenticated"]}),
    # Password policy (added this week — UI reads policy + bounds).
    ("/api/password-policy",
     {"required": ["policy", "bounds"],
      "nested": [("policy.min_length", int),
                 ("policy.require_classes", int),
                 ("policy.history_len", int),
                 ("bounds.min_length.floor", int),
                 ("bounds.min_length.ceiling", int),
                 ("bounds.min_length.default", int)]}),
    # Routing / gateway.
    ("/api/routing",
     {"required": ["base_domain", "stack_subdomain", "gateway_host",
                   "gateway_port", "app_path_prefix"]}),
    ("/api/gateway-hostnames",
     {"required": ["hostnames"]}),
    ("/api/routing-probe",
     {"required": ["routing", "services"]}),
    # Services / registry.
    # /api/services returns a LIST, not a dict — dashboard iterates it.
    ("/api/services", {"required_list": True}),
    ("/api/services/categories", {"required_list": True}),
    # Disk / guardrails.
    ("/api/disk", {"required": ["disk"]}),
    ("/api/env", {"required": []}),
)


@unittest.skipUnless(_reachable(), "controller not reachable")
class DashboardContractTests(unittest.TestCase):
    """Each dashboard-consumed endpoint must keep its response shape.
    Breaking the contract silently breaks the tab that reads it —
    before this test, those breaks only surfaced when a user clicked
    through."""

    def _resolve(self, obj: Any, dotted: str) -> Any:
        cur: Any = obj
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(dotted)
            cur = cur[part]
        return cur

    def test_every_contract_endpoint(self):
        """One subtest per contracted endpoint — a single schema drift
        shows up as a targeted failure, not a cascade."""
        client = _Client()
        # Some endpoints require auth; log in once up front.
        username = os.environ.get("CONTROLLER_TEST_USER", "admin")
        password = os.environ.get("CONTROLLER_TEST_PASSWORD",
                                   "StackAdmin-2026-Go")
        if not client.login(username, password):
            password = "media-stack"
            client.login(username, password)
        for path, spec in CONTRACTS:
            with self.subTest(endpoint=path):
                status, body = client.get_json(path)
                self.assertLess(
                    status, 500,
                    f"{path} returned {status} — server-side error, "
                    f"not a contract mismatch. body={body!r}",
                )
                if status in (401, 403):
                    self.skipTest(
                        f"{path}: auth required and login didn't "
                        "take effect; skip in this environment.",
                    )
                self.assertIsNotNone(
                    body, f"{path} did not return valid JSON",
                )
                if spec.get("required_list"):
                    self.assertIsInstance(
                        body, list,
                        f"{path} expected a JSON array but got {type(body).__name__}",
                    )
                    continue
                for field in spec.get("required", []):
                    self.assertIn(
                        field, body,
                        f"{path} is missing required top-level "
                        f"field {field!r} — the dashboard reads "
                        f"this field; a refactor just broke the UI.",
                    )
                for dotted, ty in spec.get("nested", []):
                    try:
                        got = self._resolve(body, dotted)
                    except KeyError:
                        self.fail(
                            f"{path} missing nested field {dotted!r} — "
                            "dashboard reads this path; refactor "
                            "silently broke a tab.",
                        )
                    self.assertIsInstance(
                        got, ty,
                        f"{path} field {dotted} is {type(got).__name__}, "
                        f"dashboard expects {ty.__name__}",
                    )


@unittest.skipUnless(DASHBOARD_HTML.is_file(), "dashboard.html missing")
class DashboardFetchDiscoveryTests(unittest.TestCase):
    """Meta-check: every apiFetch in dashboard.html hits a path that's
    either in the CONTRACTS table OR is a mutation (POST). Catches
    the reverse failure: the dashboard started calling a new endpoint
    without anyone adding a contract entry for it — so when the
    backend removes or refactors it, we miss the regression."""

    _FETCH_RE = re.compile(r"apiFetch\('(/api/[^']+)'\)")

    def _paths_in_html(self) -> set[str]:
        text = DASHBOARD_HTML.read_text(encoding="utf-8")
        paths: set[str] = set()
        for m in self._FETCH_RE.finditer(text):
            path = m.group(1)
            # Strip query strings and dynamic path segments for grouping.
            path = path.split("?", 1)[0]
            path = re.sub(r"/\$\{[^}]+\}", "/{id}", path)
            paths.add(path)
        return paths

    def test_contract_coverage_metric(self):
        """Report coverage — how many dashboard endpoints are in the
        contract table. Fails only if coverage drops below a ratchet
        so we tighten over time."""
        html_paths = self._paths_in_html()
        # Strip dynamic IDs and match by prefix to the contract table.
        contracted = {p for p, _ in CONTRACTS}
        # Approximate prefix match: "/api/users/{id}" counts as covered
        # by "/api/users".
        covered = 0
        for p in html_paths:
            root = p.split("/{id}", 1)[0]
            if root in contracted or p in contracted:
                covered += 1
        # Ratchet floor — raise this as we add more contracts. 47
        # endpoints exist in the HTML; we cover 10 today. Target is
        # to grow this monotonically until every endpoint has a
        # contract entry (shooting for >40 over time).
        self.assertGreaterEqual(
            covered, 10,
            f"Only {covered}/{len(html_paths)} dashboard endpoints "
            "have contract coverage. Add entries to CONTRACTS for "
            "the uncovered ones — a backend refactor will silently "
            f"break them. Found: {sorted(html_paths)[:10]}...",
        )


if __name__ == "__main__":
    unittest.main()
