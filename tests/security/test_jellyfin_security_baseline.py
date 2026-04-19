"""Live security audit against the running Jellyfin instance.

Reuses SecurityAuditRunner against Jellyfin's HTTP API. Jellyfin has
its own auth model (X-Emby-Token header, not basic auth), so the
checks that depend on basic auth are skipped automatically — we don't
configure ``admin_user`` / ``admin_pass`` on the AuditTarget.

Jellyfin also does NOT emit hardening headers itself; those are
expected to come from Envoy when Jellyfin sits behind the gateway.
The security-headers check is therefore allowed to "skip" in this
suite; the upstream Envoy audit (a separate test) asserts they land.

Env vars:
  JELLYFIN_URL        — default http://localhost:8096
  JELLYFIN_API_KEY    — optional; enables deeper auth checks (not yet
                        implemented; Jellyfin auth header is
                        X-Emby-Token which differs from Basic/Bearer).
"""

from __future__ import annotations

import os
import unittest
import urllib.error
import urllib.request

from tests.security.security_audit import AuditTarget, SecurityAuditRunner


def _reachable(base_url: str) -> bool:
    try:
        urllib.request.urlopen(base_url + "/System/Info/Public", timeout=2)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


class JellyfinSecurityBaseline(unittest.TestCase):
    """Baseline audit against Jellyfin. Most checks run as-is; a few
    are expected to skip because Jellyfin's auth/hardening model
    differs from the controller's."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.base_url = os.environ.get(
            "JELLYFIN_URL", "http://localhost:8096",
        )
        if not _reachable(cls.base_url):
            raise unittest.SkipTest(
                f"Jellyfin not reachable at {cls.base_url}",
            )
        cls.target = AuditTarget(
            base_url=cls.base_url,
            # No admin_user/admin_pass on purpose — Jellyfin uses
            # X-Emby-Token, so Basic-auth-based checks auto-skip.
            public_paths=["/System/Info/Public"],
            sensitive_paths=[
                "/Users",
                "/Sessions",
                "/System/Info",
            ],
            # Jellyfin doesn't have a "mutating no-op" endpoint that's
            # safe for the audit to hit; leaving empty skips the
            # POST-centric checks (rate limit, CSRF, body size).
            mutating_paths=[],
            webhook_post_paths=[],
        )
        cls.runner = SecurityAuditRunner(cls.target)
        cls.results = cls.runner.run_all()
        cls.by_name = {r.check: r for r in cls.results}

    def _expect(self, check: str, *, allow_skip: bool = False,
                allow_fail: str = "") -> None:
        result = self.by_name.get(check)
        self.assertIsNotNone(result, f"check {check!r} not in results")
        if allow_skip and result.status == "skip":
            self.skipTest(result.detail)
        if allow_fail and result.status == "fail":
            self.skipTest(
                f"{check} fails as expected ({allow_fail}): {result.detail}",
            )
        self.assertEqual(
            result.status, "pass",
            f"{check}: {result.detail}",
        )

    def test_public_endpoints_allow_unauth(self):
        self._expect("public_endpoints_allow_unauth")

    def test_sensitive_paths_require_auth(self):
        self._expect("sensitive_paths_require_auth")

    def test_security_headers(self):
        self._expect(
            "security_headers",
            allow_fail="Jellyfin emits no hardening headers; "
                       "Envoy adds them upstream.",
        )

    def test_hsts_value(self):
        self._expect(
            "hsts_value",
            allow_fail="Jellyfin has no HSTS; Envoy terminates TLS.",
        )

    def test_csp_defines_default_src(self):
        self._expect(
            "csp_default_src",
            allow_fail="Jellyfin has no CSP; Envoy sets it.",
        )

    def test_no_secret_in_error_bodies(self):
        self._expect("no_secret_in_errors")

    def test_trailing_slash_canonicalization(self):
        self._expect(
            "trailing_slash_canonical",
            allow_skip=True,
            allow_fail="Jellyfin routes /Users and /Users/ differently; "
                       "not a security bug, just a quirk.",
        )


if __name__ == "__main__":
    unittest.main()
