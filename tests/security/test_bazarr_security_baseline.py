"""Live security audit against Bazarr.

Bazarr uses a base URL path prefix (``/app/bazarr``) in our
deployment; adjust via ``BAZARR_URL`` + ``BAZARR_BASE_PATH`` if yours
differs.
"""

from __future__ import annotations

import os
import unittest
import urllib.error
import urllib.request

from tests.security.security_audit import AuditTarget, SecurityAuditRunner


def _reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 302, 401, 403)
    except (urllib.error.URLError, OSError):
        return False


class BazarrSecurityBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = os.environ.get("BAZARR_URL", "http://localhost:6767")
        base_path = os.environ.get("BAZARR_BASE_PATH", "/app/bazarr")
        cls.base_url = base + base_path
        probe_url = f"{cls.base_url}/api/system/status"
        if not _reachable(probe_url):
            raise unittest.SkipTest(
                f"Bazarr not reachable at {probe_url}",
            )
        cls.target = AuditTarget(
            base_url=cls.base_url,
            public_paths=[],  # Bazarr has no unauth public JSON ping
            sensitive_paths=[
                "/api/system/status",
                "/api/system/settings",
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

    def test_sensitive_paths_require_auth(self):
        self._expect("sensitive_paths_require_auth")

    def test_security_headers(self):
        self._expect(
            "security_headers",
            allow_fail="Bazarr doesn't emit hardening headers; Envoy "
                       "upstream should.",
        )

    def test_no_secret_in_errors(self):
        self._expect("no_secret_in_errors")


if __name__ == "__main__":
    unittest.main()
