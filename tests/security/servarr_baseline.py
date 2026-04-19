"""Shared baseline harness for the Servarr family (Sonarr, Radarr,
Prowlarr, Lidarr, Readarr). All of these share the same *arr auth
model: API key via ``X-Api-Key`` header, with /api/v3/* sensitive and
returning 401 without a key.

Per-app test files (test_sonarr_…, test_radarr_…, etc.) provide the
env-var + base URL and inherit ServarrSecurityBaselineMixin.
"""

from __future__ import annotations

import os
import unittest
import urllib.error
import urllib.request

from tests.security.security_audit import AuditTarget, SecurityAuditRunner


class ServarrSecurityBaselineMixin:
    """Subclass and define ``SERVICE_NAME`` + ``DEFAULT_URL`` + optional
    ``ENV_VAR`` on a ``unittest.TestCase`` subclass."""

    SERVICE_NAME: str = "servarr"
    DEFAULT_URL: str = ""
    ENV_VAR: str = ""
    API_V: str = "v3"

    @classmethod
    def _reachable(cls, base_url: str) -> bool:
        try:
            urllib.request.urlopen(
                base_url + f"/api/{cls.API_V}/system/status", timeout=2,
            )
        except urllib.error.HTTPError as exc:
            # 401 means the endpoint exists but wants auth — still reachable
            return exc.code in (401, 403, 200)
        except (urllib.error.URLError, OSError):
            return False
        return True

    @classmethod
    def _set_up_suite(cls) -> None:
        cls.base_url = os.environ.get(cls.ENV_VAR, cls.DEFAULT_URL)
        if not cls._reachable(cls.base_url):
            raise unittest.SkipTest(
                f"{cls.SERVICE_NAME} not reachable at {cls.base_url}",
            )
        cls.target = AuditTarget(
            base_url=cls.base_url,
            public_paths=[],  # Servarr apps don't expose a /ping without auth
            sensitive_paths=[
                f"/api/{cls.API_V}/system/status",
                f"/api/{cls.API_V}/config/host",
                f"/api/{cls.API_V}/tag",
            ],
            mutating_paths=[],  # API-key auth, not covered by our CSRF checks
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

    def test_sensitive_paths_require_auth(self):
        self._expect("sensitive_paths_require_auth")

    def test_security_headers(self):
        self._expect(
            "security_headers",
            allow_fail=f"{self.SERVICE_NAME} doesn't emit hardening "
                       "headers; Envoy adds them upstream.",
        )

    def test_no_secret_in_errors(self):
        self._expect("no_secret_in_errors")

    def test_trailing_slash_canonicalization(self):
        self._expect(
            "trailing_slash_canonical",
            allow_fail=f"{self.SERVICE_NAME} routes trailing slash "
                       "differently; documented quirk.",
        )
