"""Authorization tests — non-admin roles must be blocked from admin
actions at the HTTP layer, regardless of valid auth.

The failure these catch: an endpoint reads the session/remote-user
identity but never checks the role. A non-admin logged in via
Authelia could then POST /api/users/X/delete and succeed.

Runs against the live controller. Skips cleanly when unreachable.
"""

from __future__ import annotations

import json
import os
import ssl
import unittest
import urllib.parse
from http.client import HTTPConnection, HTTPSConnection


def _base() -> tuple[str, int, bool]:
    url = os.environ.get("CONTROLLER_URL", "http://127.0.0.1:9100")
    p = urllib.parse.urlsplit(url)
    return (p.hostname or "127.0.0.1",
            p.port or (443 if p.scheme == "https" else 80),
            p.scheme == "https")


def _reachable() -> bool:
    import socket
    host, port, _ = _base()
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


class _Client:
    def __init__(self):
        self.host, self.port, self.https = _base()
        self._cookies: dict[str, str] = {}

    def _conn(self):
        if self.https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return HTTPSConnection(self.host, self.port,
                                    context=ctx, timeout=5)
        return HTTPConnection(self.host, self.port, timeout=5)

    def req(self, method: str, path: str, *,
            body: bytes | None = None,
            extra_headers: dict | None = None) -> tuple[int, bytes]:
        conn = self._conn()
        try:
            hdr = {
                "Accept": "text/html,application/json,*/*;q=0.8",
                "User-Agent": "authz-test/1.0",
            }
            if self._cookies:
                hdr["Cookie"] = "; ".join(
                    f"{k}={v}" for k, v in self._cookies.items())
            if body is not None:
                hdr["Content-Type"] = "application/json"
                hdr["Content-Length"] = str(len(body))
            if extra_headers:
                hdr.update(extra_headers)
            conn.request(method, path, body=body, headers=hdr)
            resp = conn.getresponse()
            for name, value in resp.getheaders():
                if name.lower() == "set-cookie":
                    first = value.split(";", 1)[0].strip()
                    if "=" in first:
                        k, _, v = first.partition("=")
                        self._cookies[k] = v
            return resp.status, resp.read()
        finally:
            conn.close()

    def login(self, user: str, pw: str) -> bool:
        _, _ = self.req("GET", "/")
        body = json.dumps({"username": user, "password": pw}).encode()
        status, _ = self.req("POST", "/api/auth/login", body=body)
        return status == 200

    def csrf(self) -> str:
        return self._cookies.get("media_stack_csrf", "")


@unittest.skipUnless(_reachable(), "controller not reachable")
class UnauthenticatedAccessTests(unittest.TestCase):
    """No auth credentials → sensitive endpoints must deny.
    The default-allow class of bug means a port-exposed controller
    hands out admin access without requiring so much as a cookie."""

    def test_unauthenticated_post_users_is_denied(self):
        c = _Client()
        body = json.dumps({"email": "a@b", "username": "a",
                            "role_slug": "adult"}).encode()
        status, _ = c.req("POST", "/api/users", body=body,
                          extra_headers={"X-CSRF-Token": "bogus"})
        self.assertGreaterEqual(
            status, 400,
            f"Unauthenticated POST /api/users returned {status}. "
            "Must require auth or the whole admin plane is open.",
        )

    def test_unauthenticated_post_rotate_keys_is_denied(self):
        c = _Client()
        status, _ = c.req("POST", "/api/rotate-keys", body=b"{}",
                          extra_headers={"X-CSRF-Token": "bogus"})
        self.assertGreaterEqual(
            status, 400,
            "Unauthenticated key rotation must be rejected.",
        )

    def test_unauthenticated_post_password_policy_is_denied(self):
        c = _Client()
        status, _ = c.req("POST", "/api/password-policy",
                          body=b'{"min_length": 4}',
                          extra_headers={"X-CSRF-Token": "bogus"})
        self.assertGreaterEqual(
            status, 400,
            "Unauthenticated policy edit must be rejected — "
            "otherwise anyone can weaken the policy then register "
            "a weak account.",
        )


@unittest.skipUnless(_reachable(), "controller not reachable")
class SudoGateEnforcementTests(unittest.TestCase):
    """Authenticated but non-sudo'd admins hitting a sensitive
    endpoint must be blocked. Without this, a stolen session cookie
    walks straight into destructive operations."""

    def _authed(self) -> _Client:
        c = _Client()
        username = os.environ.get("CONTROLLER_TEST_USER", "admin")
        for pw in (os.environ.get("CONTROLLER_TEST_PASSWORD",
                                   "StackAdmin-2026-Go"),
                   "media-stack"):
            if c.login(username, pw):
                c.req("GET", "/")  # refresh CSRF
                return c
        self.skipTest("could not log in as admin")

    def test_reset_password_without_sudo_is_403(self):
        """The canonical sudo-gated endpoint. No X-Sudo-Password →
        403 with a clear error body."""
        c = self._authed()
        # Grab jane's id.
        status, body = c.req("GET", "/api/users")
        self.assertEqual(status, 200)
        users = json.loads(body.decode()).get("users", [])
        jane = next((u for u in users if u.get("username") == "jane"),
                    None)
        if not jane:
            self.skipTest("jane user not present")
        status, body = c.req(
            "POST", f"/api/users/{jane['id']}/reset-password",
            body=b"{}",
            extra_headers={"X-CSRF-Token": c.csrf()},
        )
        self.assertEqual(
            status, 403,
            f"reset-password without sudo returned {status}; "
            "expected 403. Sudo-gate is not enforcing — a stolen "
            "session cookie could reset any user's password.",
        )

    def test_password_policy_write_requires_sudo(self):
        """Weakening the policy must require re-auth — otherwise
        a session-hijacker flips min_length to 4 and registers a
        throwaway admin account."""
        c = self._authed()
        status, _ = c.req(
            "POST", "/api/password-policy",
            body=b'{"min_length": 4}',
            extra_headers={"X-CSRF-Token": c.csrf()},
        )
        self.assertEqual(
            status, 403,
            "password-policy write did not require sudo — "
            "privilege-escalation vector open.",
        )


if __name__ == "__main__":
    unittest.main()
