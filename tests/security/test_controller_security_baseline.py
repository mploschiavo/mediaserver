"""Live security audit against the running controller.

Runs the full baseline (see docs/security-baseline.md) via
SecurityAuditRunner. Skipped automatically if the controller isn't
reachable so it doesn't break unit-test CI; a dedicated security-CI
job runs this against a live cluster.

Env vars:
  CONTROLLER_URL    — default http://localhost:9100
  CONTROLLER_USER   — default "admin"
  CONTROLLER_PASS   — default "media-stack" (dev fallback)

Run manually:
  CONTROLLER_URL=http://localhost:9100 python -m pytest \\
      tests/security/test_controller_security_baseline.py -v -s
"""

from __future__ import annotations

import os
import unittest
import urllib.error
import urllib.request

from tests.security.security_audit import AuditTarget, SecurityAuditRunner


def _controller_reachable(base_url: str) -> bool:
    try:
        urllib.request.urlopen(base_url + "/healthz", timeout=2)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


class ControllerSecurityBaseline(unittest.TestCase):
    """Runs one test case per baseline check; a single integration
    test method aggregates the full run for CI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.base_url = os.environ.get(
            "CONTROLLER_URL", "http://localhost:9100",
        )
        if not _controller_reachable(cls.base_url):
            raise unittest.SkipTest(
                f"controller not reachable at {cls.base_url}",
            )
        cls.target = AuditTarget(
            base_url=cls.base_url,
            admin_user=os.environ.get("CONTROLLER_USER", "admin"),
            admin_pass=os.environ.get("CONTROLLER_PASS", "media-stack"),
            public_paths=["/healthz", "/readyz"],
            sensitive_paths=[
                "/api/users",
                "/api/roles",
                "/api/audit-log",
                "/metrics",
                "/api/me",
            ],
            mutating_paths=[
                # /api/users-bulk-import accepts an empty body as a no-op
                # which is ideal for rate-limit/csrf probes without
                # creating side-effects.
                "/api/users-bulk-import",
            ],
            webhook_post_paths=["/webhooks"],
        )
        cls.runner = SecurityAuditRunner(cls.target)
        cls.results = cls.runner.run_all()
        cls.by_name = {r.check: r for r in cls.results}

    def _assert_pass(self, check: str) -> None:
        result = self.by_name.get(check)
        self.assertIsNotNone(result, f"check {check!r} not in results")
        if result.status == "skip":
            self.skipTest(result.detail)
        self.assertEqual(
            result.status, "pass",
            f"{check} failed: {result.detail}",
        )

    # One test method per check so failures surface individually in CI.

    def test_public_endpoints_allow_unauth(self):
        self._assert_pass("public_endpoints_allow_unauth")

    def test_sensitive_paths_require_auth(self):
        self._assert_pass("sensitive_paths_require_auth")

    def test_authenticated_access_succeeds(self):
        self._assert_pass("authenticated_access_succeeds")

    def test_security_headers(self):
        self._assert_pass("security_headers")

    def test_hsts_value(self):
        self._assert_pass("hsts_value")

    def test_csp_defines_default_src(self):
        self._assert_pass("csp_default_src")

    def test_wrong_creds_rejected(self):
        self._assert_pass("wrong_creds_rejected")

    def test_csrf_blocks_cookie_request_without_token(self):
        self._assert_pass("csrf_blocks_cookie_no_token")

    def test_rate_limit_triggers(self):
        self._assert_pass("rate_limit_triggers")

    def test_body_size_cap(self):
        # Currently expected to FAIL — the controller doesn't cap body
        # size yet. Keep this test active (not skipped) so once we fix
        # it, the green turn-on is visible.
        self._assert_pass("body_size_cap")

    def test_webhook_ssrf_block(self):
        self._assert_pass("webhook_ssrf_block")

    def test_no_secret_in_error_bodies(self):
        self._assert_pass("no_secret_in_errors")

    def test_trailing_slash_canonicalization(self):
        self._assert_pass("trailing_slash_canonical")

    # Bearer-token checks skipped unless runner has tokens minted;
    # see test_controller_bearer_tokens_live.py for that flow.


if __name__ == "__main__":
    unittest.main()
