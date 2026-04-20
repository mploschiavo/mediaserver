"""Smoke test: admin can log in with the advertised default
credentials after a fresh deploy. This is THE most basic scenario
a user encounters — if this fails, nothing else matters.

The bug this would have caught (2026-04-19): Authelia's admin
password drifted from STACK_ADMIN_PASSWORD because the controller's
propagate_to_service_admins path reset admin to a random value,
but the env var still said "media-stack". User gets locked out on
a fresh deploy and nothing in the rest of the test suite notices.

Runs against the live stack. Asserts BOTH sign-in paths work:
  1. Direct controller session (localhost:9100 or direct host)
  2. Authelia portal (auth.<base>.local/api/firstfactor)
"""

from __future__ import annotations

import json
import os
import ssl
import unittest
from http.client import HTTPConnection, HTTPSConnection


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _controller_reachable() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 9100), timeout=2):
            return True
    except OSError:
        return False


def _authelia_reachable() -> bool:
    """Check auth.media-stack.local:443 reachable. Uses the host's
    resolver — on the dev box /etc/hosts maps this to 127.0.0.1."""
    import socket
    try:
        socket.gethostbyname("auth.media-stack.local")
    except socket.gaierror:
        return False
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=2):
            return True
    except OSError:
        return False


class ControllerDirectLoginSmokeTest(unittest.TestCase):
    """The `localhost:9100` path — admin signs in via the in-page
    form (no gateway). Uses STACK_ADMIN_PASSWORD env, no Authelia."""

    @unittest.skipUnless(_controller_reachable(),
                         "controller not on 127.0.0.1:9100")
    def test_admin_media_stack_default_works(self):
        """The documented default credentials work. If an operator
        reads the README and can't sign in, the whole product feels
        broken. This is the hard floor."""
        username = _env("STACK_ADMIN_USERNAME", "admin")
        password = _env("STACK_ADMIN_PASSWORD", "media-stack")
        conn = HTTPConnection("127.0.0.1", 9100, timeout=5)
        try:
            body = json.dumps({"username": username,
                                "password": password}).encode()
            conn.request("POST", "/api/auth/login", body=body, headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Accept": "application/json",
            })
            resp = conn.getresponse()
            status = resp.status
            raw = resp.read()
        finally:
            conn.close()
        self.assertEqual(
            status, 200,
            f"Direct controller login for {username!r} failed "
            f"(HTTP {status}): {raw[:120]!r}. This is the MOST "
            "basic auth scenario — nothing else in the stack "
            "matters if this doesn't work.",
        )


class AutheliaGatewayLoginSmokeTest(unittest.TestCase):
    """The `apps.media-stack.local/app/` path — admin signs in via
    Authelia. Requires the stack's self-signed cert + /etc/hosts."""

    @unittest.skipUnless(_authelia_reachable(),
                         "auth.media-stack.local not reachable")
    def test_admin_media_stack_default_works_through_authelia(self):
        """Admin credentials in Authelia's users_database must match
        STACK_ADMIN_PASSWORD. Earlier bug: propagate_to_service_admins
        rotated admin's hash in Authelia to a controller-generated
        value while env still said the old one — user locked out."""
        username = _env("STACK_ADMIN_USERNAME", "admin")
        password = _env("STACK_ADMIN_PASSWORD", "media-stack")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = HTTPSConnection("auth.media-stack.local", 443,
                                context=ctx, timeout=5)
        try:
            body = json.dumps({"username": username,
                                "password": password,
                                "keepMeLoggedIn": False}).encode()
            conn.request("POST", "/api/firstfactor", body=body,
                         headers={
                             "Content-Type": "application/json",
                             "Content-Length": str(len(body)),
                             "Host": "auth.media-stack.local",
                         })
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
        self.assertEqual(
            resp.status, 200,
            f"Authelia login returned HTTP {resp.status}: {raw[:120]!r}",
        )
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(
            data.get("status"), "OK",
            f"Authelia rejected {username!r} / {password!r}: "
            f"{data}. Admin credentials drift between "
            "STACK_ADMIN_PASSWORD env and users_database.yml — "
            "this is the 'I can't log in after deploy' bug the "
            "team hit on 2026-04-19.",
        )


class CredentialConsistencySmokeTest(unittest.TestCase):
    """The env var, the controller user store, and Authelia's
    users_database must all agree on the admin's credential. Any
    drift causes a login to work through one path and fail through
    the other — exactly the failure the user reported."""

    @unittest.skipUnless(
        _controller_reachable() and _authelia_reachable(),
        "need both controller and Authelia reachable")
    def test_controller_and_authelia_accept_same_creds(self):
        """The invariant that actually matters end-to-end: the same
        username+password works against BOTH providers. If it works
        against one but not the other, an admin's mental model
        ('this is my password') is wrong somewhere."""
        username = _env("STACK_ADMIN_USERNAME", "admin")
        password = _env("STACK_ADMIN_PASSWORD", "media-stack")

        # Controller path.
        conn = HTTPConnection("127.0.0.1", 9100, timeout=5)
        body = json.dumps({"username": username,
                            "password": password}).encode()
        try:
            conn.request("POST", "/api/auth/login", body=body, headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            })
            ctrl_status = conn.getresponse().status
        finally:
            conn.close()

        # Authelia path.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = HTTPSConnection("auth.media-stack.local", 443,
                                context=ctx, timeout=5)
        try:
            ab = json.dumps({"username": username,
                              "password": password,
                              "keepMeLoggedIn": False}).encode()
            conn.request("POST", "/api/firstfactor", body=ab,
                         headers={
                             "Content-Type": "application/json",
                             "Content-Length": str(len(ab)),
                         })
            resp = conn.getresponse()
            auth_data = json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

        self.assertEqual(ctrl_status, 200,
                         f"Controller login failed: HTTP {ctrl_status}")
        self.assertEqual(
            auth_data.get("status"), "OK",
            f"Authelia login failed while controller accepted the "
            "same creds. Drift between env / user store / "
            f"users_database.yml. Authelia said: {auth_data}",
        )


class NonAdminResetThenLoginSmokeTest(unittest.TestCase):
    """The critical flow nobody has tested: admin resets a
    non-admin user's password in the UI → that user then logs in
    with the new password on localhost:9100. Every test of
    reset-password only checked that the endpoint RETURNED a
    password, never that the returned password actually works
    for sign-in. Hours of debugging could have been a single test."""

    @unittest.skipUnless(_controller_reachable(),
                         "controller not on 127.0.0.1:9100")
    def test_reset_non_admin_then_login_round_trip(self):
        # Log in as admin first to get CSRF + sudo pw handy.
        admin_user = _env("STACK_ADMIN_USERNAME", "admin")
        admin_pw = _env("STACK_ADMIN_PASSWORD", "media-stack")

        admin_conn = HTTPConnection("127.0.0.1", 9100, timeout=5)
        cookies: dict[str, str] = {}

        def _send(method: str, path: str, *, body: bytes | None = None,
                  extra_headers: dict | None = None) -> tuple[int, bytes, dict]:
            conn = HTTPConnection("127.0.0.1", 9100, timeout=5)
            try:
                hdr = {
                    "Accept": "text/html,application/json,*/*;q=0.8",
                    "User-Agent": "reset-login-smoke/1.0",
                }
                if cookies:
                    hdr["Cookie"] = "; ".join(
                        f"{k}={v}" for k, v in cookies.items())
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
                            cookies[k] = v
                return resp.status, resp.read(), dict(resp.getheaders())
            finally:
                conn.close()

        # GET / seeds the CSRF cookie.
        _send("GET", "/")
        # Admin login.
        body = json.dumps({"username": admin_user,
                            "password": admin_pw}).encode()
        status, raw, _ = _send("POST", "/api/auth/login", body=body)
        if status != 200:
            self.skipTest(f"admin login failed: {status} {raw[:80]!r}")

        # Find a non-admin user; skip if there isn't one yet.
        status, raw, _ = _send("GET", "/api/users")
        users = json.loads(raw.decode()).get("users", [])
        target = next(
            (u for u in users
             if u.get("role_slug") != "superadmin"
             and u.get("state") == "active"),
            None)
        if not target:
            self.skipTest("no non-admin user to reset")

        # Reset the non-admin's password to a unique value so the
        # policy's no-reuse history doesn't reject us on repeat
        # runs. X-Sudo-Password is required; admin's own password
        # satisfies it.
        import uuid as _uuid
        new_pw = "SmokeTest-" + _uuid.uuid4().hex[:12] + "-Pw2026!"
        reset_body = json.dumps({"password": new_pw}).encode()
        csrf = cookies.get("media_stack_csrf", "")
        status, raw, _ = _send(
            "POST", f"/api/users/{target['id']}/reset-password",
            body=reset_body,
            extra_headers={"X-CSRF-Token": csrf,
                           "X-Sudo-Password": admin_pw})
        self.assertEqual(
            status, 200,
            f"reset-password returned HTTP {status}: {raw[:200]!r}",
        )

        # Now clear the admin session and try to log in as the
        # non-admin with the new password.
        cookies.clear()
        _send("GET", "/")  # fresh CSRF
        login_body = json.dumps({
            "username": target["username"], "password": new_pw,
        }).encode()
        status, raw, _ = _send("POST", "/api/auth/login", body=login_body)
        self.assertEqual(
            status, 200,
            f"Non-admin user {target['username']!r} could not log "
            f"in with their freshly-reset password (HTTP {status}): "
            f"{raw[:200]!r}. This is the 'I changed jane's password "
            "but she can't log in' bug — reset worked on the API "
            "side but localhost login silently rejected non-admin "
            "credentials.",
        )


class LoginAcceptsEmailOrUsernameSmokeTest(unittest.TestCase):
    """The login form says 'Username or email'. Both MUST work —
    users will type their email (it's on screen in the user list)
    before they try the username. Silently rejecting 'jane@local'
    with 'invalid credentials' while 'jane' succeeds is the exact
    UX failure the user hit."""

    @unittest.skipUnless(_controller_reachable(),
                         "controller not on 127.0.0.1:9100")
    def test_email_accepted_as_login_identifier(self):
        """End-to-end: reset a non-admin to a known password, then
        log in using the EMAIL as the identifier. Succeeds iff
        BasicAuthVerifier resolves email → user → password hash."""
        admin_user = _env("STACK_ADMIN_USERNAME", "admin")
        admin_pw = _env("STACK_ADMIN_PASSWORD", "media-stack")
        cookies: dict[str, str] = {}

        def _send(method, path, *, body=None, extra_headers=None):
            conn = HTTPConnection("127.0.0.1", 9100, timeout=5)
            try:
                hdr = {"Accept": "application/json,text/html",
                       "User-Agent": "email-login-smoke/1.0"}
                if cookies:
                    hdr["Cookie"] = "; ".join(f"{k}={v}"
                                               for k, v in cookies.items())
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
                            cookies[k] = v
                return resp.status, resp.read()
            finally:
                conn.close()

        _send("GET", "/")
        status, _ = _send("POST", "/api/auth/login",
                          body=json.dumps({"username": admin_user,
                                            "password": admin_pw}).encode())
        if status != 200:
            self.skipTest(f"admin login failed: {status}")

        # Pick a non-admin user.
        status, raw = _send("GET", "/api/users")
        users = json.loads(raw.decode()).get("users", [])
        target = next((u for u in users
                       if u.get("role_slug") != "superadmin"
                       and u.get("state") == "active"), None)
        if not target:
            self.skipTest("no non-admin user")

        import uuid as _uuid
        new_pw = "EmailLogin-" + _uuid.uuid4().hex[:12] + "-Pw!"
        csrf = cookies.get("media_stack_csrf", "")
        status, raw = _send(
            "POST", f"/api/users/{target['id']}/reset-password",
            body=json.dumps({"password": new_pw}).encode(),
            extra_headers={"X-CSRF-Token": csrf,
                           "X-Sudo-Password": admin_pw})
        self.assertEqual(status, 200,
                         f"reset-password failed: {raw[:120]!r}")

        cookies.clear()
        _send("GET", "/")  # fresh CSRF
        # Log in using EMAIL (the reported UX failure).
        status, raw = _send("POST", "/api/auth/login",
                            body=json.dumps({
                                "username": target["email"],
                                "password": new_pw}).encode())
        self.assertEqual(
            status, 200,
            f"Login with email {target['email']!r} returned HTTP "
            f"{status}: {raw[:200]!r}. The UI prompts for "
            "'Username or email' — it must accept both.",
        )


class LoopbackNotLockedOutSmokeTest(unittest.TestCase):
    """Would have caught the 2026-04-19 incident: after enough bad
    auth attempts (tests, typos, retries) the dev's loopback IP
    tripped the lockout and http://localhost:9100/ returned 429
    even with the right credentials. Loopback MUST be exempt so
    dev workflow doesn't break the login UI."""

    @unittest.skipUnless(_controller_reachable(),
                         "controller not on 127.0.0.1:9100")
    def test_bad_attempts_from_loopback_do_not_lock_out_future_good_ones(self):
        """Hammer the login with bad credentials enough to trip the
        lockout threshold (20+ failures), then verify a GOOD login
        from the same IP still works."""
        # Burn through bad attempts.
        for _ in range(25):
            conn = HTTPConnection("127.0.0.1", 9100, timeout=3)
            try:
                body = json.dumps({
                    "username": "admin",
                    "password": "definitely-wrong-" + str(_),
                }).encode()
                conn.request("POST", "/api/auth/login", body=body,
                             headers={
                                 "Content-Type": "application/json",
                                 "Content-Length": str(len(body)),
                             })
                conn.getresponse().read()
            finally:
                conn.close()

        # Now the right password — must work from loopback even
        # after the burn.
        username = _env("STACK_ADMIN_USERNAME", "admin")
        password = _env("STACK_ADMIN_PASSWORD", "media-stack")
        conn = HTTPConnection("127.0.0.1", 9100, timeout=5)
        try:
            body = json.dumps({"username": username,
                                "password": password}).encode()
            conn.request("POST", "/api/auth/login", body=body, headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            })
            resp = conn.getresponse()
            status = resp.status
            resp.read()
        finally:
            conn.close()
        self.assertEqual(
            status, 200,
            f"Loopback was locked out after bad attempts: HTTP "
            f"{status}. Dev work + test runs would permanently "
            "brick localhost:9100 until container restart.",
        )


if __name__ == "__main__":
    unittest.main()
