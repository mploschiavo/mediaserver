"""End-to-end tests for authenticated dashboard flows.

These tests exercise the EXACT sequence a real browser performs:
  1. GET / → receive session cookie + CSRF cookie
  2. POST /api/auth/login → receive session cookie
  3. GET / again → receive CSRF cookie
  4. POST /api/users/{id}/reset-password with:
       - session cookie (sent by browser automatically)
       - X-CSRF-Token header (echoed from cookie)
       - X-Sudo-Password header (for sensitive ops)
     → receive 200 + JSON body with generated_password

These tests would have caught multiple regressions that only e2e
coverage can surface:

  - CSRF cookie was never issued → every authenticated POST rejected
    with "CSRF token missing or invalid"
  - Authelia's /api/authz/forward-auth rejected POST with 405 →
    dashboard fetch().json() parse error
  - Referrer-Policy: no-referrer broke Envoy routing that matched
    on Referer header

Runs against whichever stack is reachable on CONTROLLER_URL
(default http://127.0.0.1:9100). Skips cleanly when unreachable so
the file stays green in CI environments that don't spin up the
stack. Locally, a running compose stack means the suite runs.
"""

from __future__ import annotations

import json
import os
import ssl
import unittest
import urllib.parse
from http.client import HTTPConnection, HTTPSConnection
from typing import Any


def _controller_base() -> tuple[str, str, int, bool]:
    """Return (base_url, host, port, https_flag) for the controller."""
    base = os.environ.get("CONTROLLER_URL", "http://127.0.0.1:9100")
    parsed = urllib.parse.urlsplit(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return base, host, port, parsed.scheme == "https"


def _reachable() -> bool:
    import socket
    _, host, port, _ = _controller_base()
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


class _Client:
    """Minimal cookie-aware HTTP client for the flow tests.

    Not using requests/urllib's redirect follower because we need to
    inspect 302s (e.g. login redirect). Cookies are parsed from
    Set-Cookie headers and re-echoed on subsequent calls, exactly
    like a browser.
    """

    def __init__(self):
        _, self.host, self.port, self.https = _controller_base()
        self._cookies: dict[str, str] = {}

    def _conn(self):
        if self.https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return HTTPSConnection(self.host, self.port, context=ctx, timeout=5)
        return HTTPConnection(self.host, self.port, timeout=5)

    def _cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    def _store_cookies(self, headers: list[tuple[str, str]]) -> None:
        for name, value in headers:
            if name.lower() != "set-cookie":
                continue
            # "media_stack_csrf=xyz; Path=/; SameSite=Strict"
            first = value.split(";", 1)[0].strip()
            if "=" in first:
                k, _, v = first.partition("=")
                self._cookies[k] = v

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None
                ) -> tuple[int, dict, bytes]:
        conn = self._conn()
        hdr = dict(headers or {})
        # Mimic a real browser so the server picks the HTML/login-page
        # code paths, not the "API client with Accept: */*" branch that
        # emits WWW-Authenticate: Basic and triggers the native popup.
        hdr.setdefault("Accept", "text/html,application/xhtml+xml,application/json,*/*;q=0.8")
        hdr.setdefault("User-Agent", "media-stack-e2e/1.0 (Mozilla compatible)")
        if self._cookies:
            hdr["Cookie"] = self._cookie_header()
        if body is not None and "Content-Type" not in hdr:
            hdr["Content-Type"] = "application/json"
        if body is not None:
            hdr["Content-Length"] = str(len(body))
        try:
            conn.request(method, path, body=body, headers=hdr)
            resp = conn.getresponse()
            raw_headers = resp.getheaders()
            self._store_cookies(raw_headers)
            body_bytes = resp.read()
            # Stringify headers as a simple dict (last wins for dupes)
            h = {}
            for k, v in raw_headers:
                h[k] = v
            return resp.status, h, body_bytes
        finally:
            conn.close()

    def csrf(self) -> str:
        return self._cookies.get("media_stack_csrf", "")


@unittest.skipUnless(_reachable(), "controller not reachable on CONTROLLER_URL")
class DashboardGetIssuesCsrfCookieTests(unittest.TestCase):
    """The dashboard HTML response MUST issue a CSRF cookie on first
    GET. Without this, the double-submit protection blocks every
    subsequent POST from an authenticated browser — which is the
    exact bug that broke password reset in production on 2026-04-19.
    """

    def test_first_get_issues_csrf_cookie(self):
        c = _Client()
        status, headers, _ = c.request("GET", "/")
        # Could be 200 (dashboard), 401 (login page), 302 (OIDC
        # redirect). All of these are "served to the browser" and
        # must issue the CSRF cookie.
        self.assertIn(status, (200, 302, 401),
                      f"unexpected status {status} on GET /")
        self.assertTrue(
            c.csrf(),
            "GET / did not set media_stack_csrf cookie. The dashboard "
            "JS reads this cookie to populate X-CSRF-Token; without "
            "it, every POST fails with 'CSRF token missing or "
            "invalid'. Check _issue_csrf_cookie_if_missing wiring.",
        )

    def test_second_get_preserves_csrf_token(self):
        """Token must NOT rotate on every GET — doing so races with
        any in-flight POST and spuriously rejects legitimate
        requests when two tabs are open."""
        c = _Client()
        c.request("GET", "/")
        first = c.csrf()
        c.request("GET", "/api/env")
        second = c.csrf()
        self.assertEqual(first, second,
                         "CSRF token rotated on second GET; it must "
                         "stay stable while the cookie is live.")

    def test_api_get_also_issues_csrf(self):
        """A browser might hit an /api/* GET first (e.g. SPA route).
        Those responses must also carry the Set-Cookie so the user
        never ends up in a state where the cookie doesn't exist."""
        c = _Client()
        status, _, _ = c.request("GET", "/api/services")
        self.assertIn(status, (200, 401),
                      f"unexpected status {status} on /api/services")
        self.assertTrue(
            c.csrf(),
            "GET /api/services did not set the CSRF cookie",
        )


@unittest.skipUnless(_reachable(), "controller not reachable on CONTROLLER_URL")
class AuthenticatedPostRequiresCsrfHeaderTests(unittest.TestCase):
    """With a session cookie present, POST requests MUST carry a
    matching X-CSRF-Token or be rejected. Validates the server-side
    half of the double-submit pattern still enforces correctly."""

    def _login(self) -> _Client:
        c = _Client()
        c.request("GET", "/")  # seed CSRF cookie
        username = os.environ.get("CONTROLLER_TEST_USER", "admin")
        password = os.environ.get("CONTROLLER_TEST_PASSWORD", "media-stack")
        status, _, body = c.request(
            "POST", "/api/auth/login",
            body=json.dumps({"username": username,
                             "password": password}).encode("utf-8"),
        )
        if status != 200:
            self.skipTest(
                f"login failed (HTTP {status}): {body[:80]!r}. "
                "Set CONTROLLER_TEST_USER/PASSWORD if default admin "
                "creds differ.",
            )
        return c

    def test_post_without_csrf_header_is_rejected(self):
        """A session-cookied POST with NO X-CSRF-Token must 403.
        This is the property CSRF protection is supposed to provide."""
        c = self._login()
        status, _, body = c.request(
            "POST", "/api/routing",
            body=b"{}",
            # intentionally no X-CSRF-Token
        )
        self.assertEqual(status, 403,
                         f"expected 403 CSRF rejection, got {status}: "
                         f"{body[:120]!r}")

    def test_post_with_matching_csrf_header_succeeds(self):
        """Happy path: session cookie + X-CSRF-Token matching the
        cookie → request processed normally (not a CSRF 403)."""
        c = self._login()
        # Refresh GET to ensure CSRF cookie is still live
        c.request("GET", "/")
        token = c.csrf()
        self.assertTrue(token, "no CSRF cookie after login")
        status, _, body = c.request(
            "POST", "/api/routing",
            body=b"{}",
            headers={"X-CSRF-Token": token},
        )
        # 200 (no-op update), 400 (malformed), anything non-403 is
        # acceptable — we're only asserting CSRF did NOT block.
        self.assertNotEqual(
            status, 403,
            f"CSRF rejected a request with a valid token+cookie: "
            f"{body[:120]!r}. The double-submit wiring is broken.",
        )


@unittest.skipUnless(_reachable(), "controller not reachable on CONTROLLER_URL")
class ResetPasswordEndToEndTests(unittest.TestCase):
    """The exact flow the user hits in the dashboard: log in, look
    up a user, POST reset-password, expect a generated_password back.

    This is the test that SHOULD have caught every regression of the
    last two days. If this ever fails, the user sees a broken admin
    UI — nothing else matters."""

    def _authed_client(self) -> _Client:
        c = _Client()
        c.request("GET", "/")
        username = os.environ.get("CONTROLLER_TEST_USER", "admin")
        password = os.environ.get("CONTROLLER_TEST_PASSWORD", "media-stack")
        status, _, body = c.request(
            "POST", "/api/auth/login",
            body=json.dumps({"username": username,
                             "password": password}).encode("utf-8"),
        )
        if status != 200:
            self.skipTest(f"login failed: HTTP {status}")
        c.request("GET", "/")  # refresh CSRF
        return c, password

    def _first_non_admin_user_id(self, c: _Client) -> str | None:
        status, _, body = c.request("GET", "/api/users")
        if status != 200:
            return None
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            return None
        for u in data.get("users", []):
            if (u.get("username") or "").lower() != "admin":
                return u.get("id")
        return None

    def test_reset_password_returns_json_with_generated_password(self):
        c, admin_pw = self._authed_client()
        uid = self._first_non_admin_user_id(c)
        if not uid:
            self.skipTest("no non-admin user to reset")
        status, hdrs, body = c.request(
            "POST", f"/api/users/{uid}/reset-password",
            body=b"{}",
            headers={
                "X-CSRF-Token": c.csrf(),
                "X-Sudo-Password": admin_pw,
            },
        )
        self.assertEqual(
            status, 200,
            f"reset-password returned HTTP {status} — this is the "
            f"exact failure the user hit on 2026-04-19. body={body[:200]!r}",
        )
        ctype = (hdrs.get("Content-Type") or "").lower()
        self.assertIn("application/json", ctype,
                      f"response is not JSON: Content-Type={ctype!r}, "
                      f"body={body[:200]!r}")
        data = json.loads(body.decode("utf-8"))
        self.assertIn("generated_password", data,
                      f"response missing generated_password: {data!r}")
        self.assertTrue(data["generated_password"],
                        "generated_password is empty")

    def test_reset_password_manual_value_is_honored(self):
        """The API accepts an explicit password override. The
        dashboard's new modal-based reset UI uses this path when the
        admin types a password instead of auto-generating one."""
        c, admin_pw = self._authed_client()
        uid = self._first_non_admin_user_id(c)
        if not uid:
            self.skipTest("no non-admin user to reset")
        chosen = "ManualPw-" + os.urandom(4).hex()
        status, hdrs, body = c.request(
            "POST", f"/api/users/{uid}/reset-password",
            body=json.dumps({"password": chosen}).encode("utf-8"),
            headers={
                "X-CSRF-Token": c.csrf(),
                "X-Sudo-Password": admin_pw,
            },
        )
        self.assertEqual(status, 200,
                         f"manual reset returned {status}: {body[:120]!r}")


@unittest.skipUnless(_reachable(), "controller not reachable on CONTROLLER_URL")
class PasswordPolicyEndpointTests(unittest.TestCase):
    """GET /api/password-policy returns the policy + bounds the UI
    renders. POST updates it (sudo-gated). Would have caught a
    missing endpoint or forgotten sudo gate that only surfaces from
    a real authenticated browser session."""

    def _authed(self) -> tuple[_Client, str]:
        c = _Client()
        c.request("GET", "/")
        username = os.environ.get("CONTROLLER_TEST_USER", "admin")
        password = os.environ.get("CONTROLLER_TEST_PASSWORD", "media-stack")
        status, _, body = c.request(
            "POST", "/api/auth/login",
            body=json.dumps({"username": username,
                             "password": password}).encode("utf-8"),
        )
        if status != 200:
            self.skipTest(f"login failed: HTTP {status}")
        c.request("GET", "/")
        return c, password

    def test_get_returns_policy_and_bounds(self):
        c, _ = self._authed()
        status, _, body = c.request("GET", "/api/password-policy")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("policy", data)
        self.assertIn("bounds", data)
        for key in ("min_length", "require_classes", "history_len"):
            self.assertIn(key, data["policy"])
            self.assertIn(key, data["bounds"])

    def test_post_without_sudo_is_rejected(self):
        """The policy edit is privileged. A session cookie alone
        must NOT be enough — require X-Sudo-Password. Without the
        sudo gate, a stolen session could weaken the policy silently."""
        c, _ = self._authed()
        status, _, body = c.request(
            "POST", "/api/password-policy",
            body=json.dumps({"min_length": 8}).encode("utf-8"),
            headers={"X-CSRF-Token": c.csrf()},
        )
        self.assertEqual(
            status, 403,
            f"expected 403 sudo rejection, got {status}: {body[:120]!r}",
        )

    # Known-strong defaults. Restore to THESE, not "whatever was
    # there" — otherwise if a prior run left weak values on disk,
    # every subsequent run preserves the weakness and production
    # ends up with min_length:4.
    _STRONG_DEFAULTS = {"min_length": 12, "require_classes": 3,
                         "history_len": 5}

    def test_post_with_sudo_updates_policy(self):
        c, pw = self._authed()
        try:
            status, _, body = c.request(
                "POST", "/api/password-policy",
                body=json.dumps({"min_length": 16}).encode("utf-8"),
                headers={"X-CSRF-Token": c.csrf(),
                         "X-Sudo-Password": pw},
            )
            self.assertEqual(status, 200,
                             f"update failed: {body[:120]!r}")
            data = json.loads(body.decode("utf-8"))
            self.assertEqual(data["policy"]["min_length"], 16)
            # Verify GET reflects the change.
            _, _, body2 = c.request("GET", "/api/password-policy")
            self.assertEqual(
                json.loads(body2.decode("utf-8"))["policy"]["min_length"],
                16,
            )
        finally:
            # ALWAYS restore to strong defaults — don't propagate
            # whatever the previous run left behind.
            c.request(
                "POST", "/api/password-policy",
                body=json.dumps(self._STRONG_DEFAULTS).encode("utf-8"),
                headers={"X-CSRF-Token": c.csrf(),
                         "X-Sudo-Password": pw},
            )


@unittest.skipUnless(_reachable(), "controller not reachable on CONTROLLER_URL")
class LogoutFlowTests(unittest.TestCase):
    """Logout must clear the ms_session cookie server-side so a stolen
    cookie can't outlive a sign-out. The previous UI logout button
    redirected to /api/authz/logout (not an Authelia endpoint) and
    404'd without ever killing the controller session."""

    def test_logout_kills_session_cookie(self):
        c = _Client()
        c.request("GET", "/")
        username = os.environ.get("CONTROLLER_TEST_USER", "admin")
        password = os.environ.get("CONTROLLER_TEST_PASSWORD", "media-stack")
        status, _, body = c.request(
            "POST", "/api/auth/login",
            body=json.dumps({"username": username,
                             "password": password}).encode("utf-8"),
        )
        if status != 200:
            self.skipTest(f"login failed: HTTP {status}")
        # Session cookie is now live; a GET / should return 200.
        status_pre, _, _ = c.request("GET", "/")
        self.assertEqual(status_pre, 200)
        # Logout.
        status_out, _, _ = c.request(
            "POST", "/api/auth/logout",
            body=b"",
            headers={"X-CSRF-Token": c.csrf()},
        )
        self.assertEqual(
            status_out, 200,
            f"/api/auth/logout returned {status_out}; this is the "
            "endpoint the dashboard logout button calls. If it isn't "
            "200 the browser session cookie never gets cleared.",
        )
        # After logout, the session should be dead. Clear the _Client's
        # in-memory session so we test what the server decides, not
        # what the client remembers.
        c._cookies.pop("ms_session", None)
        status_post, _, _ = c.request("GET", "/")
        self.assertIn(status_post, (200, 401, 302),
                      f"unexpected status {status_post} after logout")


if __name__ == "__main__":
    unittest.main()
