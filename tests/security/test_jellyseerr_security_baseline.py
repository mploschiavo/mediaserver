"""Live security audit against the running Jellyseerr instance.

Env:
  JELLYSEERR_URL — default http://localhost:5055
"""

from __future__ import annotations

import os
import unittest
import urllib.error
import urllib.request

from tests.security.security_audit import AuditTarget, SecurityAuditRunner


def _reachable(base_url: str) -> bool:
    try:
        urllib.request.urlopen(base_url + "/api/v1/status", timeout=2)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


class JellyseerrSecurityBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_url = os.environ.get(
            "JELLYSEERR_URL", "http://localhost:5055",
        )
        if not _reachable(cls.base_url):
            raise unittest.SkipTest(
                f"Jellyseerr not reachable at {cls.base_url}",
            )
        cls.target = AuditTarget(
            base_url=cls.base_url,
            public_paths=["/api/v1/status"],
            sensitive_paths=[
                "/api/v1/user",
                "/api/v1/settings/main",
                "/api/v1/auth/me",
            ],
            mutating_paths=[],
            webhook_post_paths=[],
        )
        cls.runner = SecurityAuditRunner(cls.target)
        cls.results = cls.runner.run_all()
        cls.by_name = {r.check: r for r in cls.results}

    def _expect(self, check: str, *, allow_fail: str = "") -> None:
        result = self.by_name.get(check)
        self.assertIsNotNone(result, f"check {check!r} missing")
        if result.status == "skip":
            self.skipTest(result.detail)
        if allow_fail and result.status == "fail":
            self.skipTest(f"{check}: {allow_fail} ({result.detail})")
        self.assertEqual(result.status, "pass",
                         f"{check}: {result.detail}")

    def test_public_endpoint_allows_unauth(self):
        self._expect("public_endpoints_allow_unauth")

    def test_sensitive_paths_require_auth(self):
        self._expect("sensitive_paths_require_auth")

    def test_security_headers(self):
        self._expect(
            "security_headers",
            allow_fail="Envoy upstream is expected to emit hardening "
                       "headers; Jellyseerr itself doesn't.",
        )

    def test_no_secret_in_errors(self):
        self._expect("no_secret_in_errors")


if __name__ == "__main__":
    unittest.main()
