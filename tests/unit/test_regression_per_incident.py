"""One test per past production incident — locks in the original
failure mode so the exact bug can't come back unnoticed.

Each test references the memory file that documents the incident
so a future reader can trace back to the why.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class TlsRegenRegressionTests(unittest.TestCase):
    """See project_envoy_tls_regen_bug.md.

    Root cause: the controller's in-process Envoy regen silently
    emitted a plain-HTTP config when it couldn't find the cert,
    overwriting the good TLS config on disk. Three defenses were
    added: /certs mount on the controller, TlsRegressionGuard, and
    diagnostic logging. This test exercises the GUARD specifically."""

    def test_guard_blocks_tls_to_plain_downgrade(self):
        from media_stack.core.platforms.compose.edge.providers.envoy.tls_regression_guard import (
            TlsRegressionGuard,
        )
        with tempfile.TemporaryDirectory() as d:
            existing = Path(d) / "envoy.yaml"
            existing.write_text(
                "static_resources:\n"
                "  listeners:\n"
                "  - filter_chains:\n"
                "    - transport_socket:\n"
                "        name: tls\n",
                encoding="utf-8",
            )
            plain_payload = {
                "static_resources": {
                    "listeners": [{"filter_chains": [{"filters": []}]}],
                },
            }
            self.assertTrue(
                TlsRegressionGuard().would_lose_tls(existing, plain_payload),
                "Guard failed to flag a TLS→plain downgrade — "
                "the original bug is back.",
            )

    def test_guard_allows_plain_to_plain(self):
        """A fresh k8s install with no transport_socket on disk
        and none in the payload is NOT a regression — guard must
        not flag it."""
        from media_stack.core.platforms.compose.edge.providers.envoy.tls_regression_guard import (
            TlsRegressionGuard,
        )
        with tempfile.TemporaryDirectory() as d:
            existing = Path(d) / "envoy.yaml"
            existing.write_text(
                "static_resources:\n  listeners: []\n",
                encoding="utf-8",
            )
            plain_payload = {"static_resources": {"listeners": []}}
            self.assertFalse(
                TlsRegressionGuard().would_lose_tls(existing, plain_payload),
                "Guard flagged a plain→plain regen as regression — "
                "would break every k8s deployment.",
            )


class CsrfCookieIssuedOnGetRegressionTests(unittest.TestCase):
    """See project_session_resume / feedback_test_real_dashboard_flows.

    Root cause: the CSRF `build_set_cookie()` function was written
    but never called. Every authenticated POST from a browser
    failed with 'CSRF token missing or invalid'. The fix wired
    _issue_csrf_if_missing into _json_response and _html_response."""

    def test_issue_helper_emits_cookie_when_missing(self):
        """Simulated handler: no Cookie header on request → helper
        must call send_header('Set-Cookie', '...'). The reverse
        bug (emit on every GET even when present) would rotate
        the token mid-flight and reject valid POSTs."""
        from media_stack.api.server import _issue_csrf_if_missing

        class _Handler:
            command = "GET"

            def __init__(self):
                self.headers = _NoCookieHeaders()
                self.sent: list = []

            def send_header(self, name, value):
                self.sent.append((name, value))

        class _NoCookieHeaders:
            def get(self, name, default=""):
                return default

        h = _Handler()
        _issue_csrf_if_missing(h)
        cookie_hdrs = [v for n, v in h.sent if n == "Set-Cookie"]
        self.assertEqual(
            len(cookie_hdrs), 1,
            "GET without a CSRF cookie did not emit Set-Cookie. "
            "Double-submit protection is broken — every authed "
            "POST from a browser will 403.",
        )
        self.assertIn("media_stack_csrf", cookie_hdrs[0])

    def test_issue_helper_does_not_rotate_when_already_set(self):
        from media_stack.api.server import _issue_csrf_if_missing

        class _Handler:
            command = "GET"

            def __init__(self):
                self.headers = _HasCookie()
                self.sent: list = []

            def send_header(self, name, value):
                self.sent.append((name, value))

        class _HasCookie:
            def get(self, name, default=""):
                if name == "Cookie":
                    return "media_stack_csrf=stable-token-value"
                return default

        h = _Handler()
        _issue_csrf_if_missing(h)
        cookie_hdrs = [v for n, v in h.sent if n == "Set-Cookie"]
        self.assertEqual(
            cookie_hdrs, [],
            "GET rotated the CSRF cookie even though one was "
            "already set — a concurrent POST would see the old "
            "cookie and the new header, mismatch, reject.",
        )

    def test_post_does_not_emit_csrf_cookie(self):
        """Only GETs issue tokens. A POST that also sets the cookie
        would race with itself if the caller reads the Set-Cookie
        response header while preparing a follow-up POST."""
        from media_stack.api.server import _issue_csrf_if_missing

        class _PostHandler:
            command = "POST"

            def __init__(self):
                self.headers = _NoCookie()
                self.sent: list = []

            def send_header(self, name, value):
                self.sent.append((name, value))

        class _NoCookie:
            def get(self, name, default=""):
                return default

        h = _PostHandler()
        _issue_csrf_if_missing(h)
        self.assertEqual(h.sent, [])


class LogoutRedirectsToAutheliaRegressionTests(unittest.TestCase):
    """See session memory: logout previously redirected to
    /api/authz/logout which is NOT an Authelia endpoint — user
    hit 404. Fix: use Authelia's real /logout path and call the
    controller's /api/auth/logout first."""

    def test_authelia_logout_endpoint_is_slash_logout(self):
        """The dashboard JS builds a URL pointing at the Authelia
        host's /logout. The REGRESSION was /api/authz/logout (which
        was never Authelia's endpoint). This check is a grep — if
        someone puts the bad path back, the test fails."""
        html = (ROOT / "src" / "media_stack" / "api"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn(
            "/api/authz/logout", html,
            "Dashboard references the non-existent Authelia endpoint "
            "/api/authz/logout — logout will 404 for browser users.",
        )
        self.assertIn(
            "/logout?rd=", html,
            "Dashboard missing the Authelia /logout redirect. Sign-out "
            "clicks won't kill the SSO session.",
        )


class AutheliaUsersDatabasePreservedRegressionTests(unittest.TestCase):
    """See project_session_2026_04_19 / earlier preservation tests.

    Root cause: configure-auth regenerated users_database.yml with
    ONLY the admin entry, wiping every other user's password on
    every auth-config edit. Fix lives in
    AutheliaConfigGenerator.write_config (merge path)."""

    def test_existing_non_admin_user_survives_regen(self):
        from media_stack.core.auth.authelia_config_generator import (
            AutheliaConfigGenerator,
            AutheliaConfigOptions,
        )
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            opts = AutheliaConfigOptions(
                base_domain="local",
                stack_subdomain="media-stack",
                gateway_host="apps.media-stack.local",
                gateway_port=443,
                internet_exposed=False,
                admin_username="admin",
                admin_email="admin@local",
            )
            AutheliaConfigGenerator(opts).write_config(out)
            # Self-heal adds jane between regens.
            users_path = out / "users_database.yml"
            data = yaml.safe_load(users_path.read_text())
            data["users"]["jane"] = {
                "displayname": "Jane", "email": "jane@local",
                "password": "$argon2id$LIVE_HASH",
                "groups": ["users"],
            }
            users_path.write_text(yaml.safe_dump(data), encoding="utf-8")
            # Second regen.
            AutheliaConfigGenerator(opts).write_config(out)
            after = yaml.safe_load(users_path.read_text())
            self.assertIn(
                "jane", after["users"],
                "jane's row wiped on regen — 'I reset her password "
                "but she can't log in' bug is back.",
            )
            self.assertEqual(
                after["users"]["jane"]["password"],
                "$argon2id$LIVE_HASH",
                "jane's password hash overwritten on regen.",
            )


if __name__ == "__main__":
    unittest.main()
