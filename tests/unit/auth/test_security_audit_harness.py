"""Unit tests for SecurityAuditRunner's decision logic.

We don't hit a live service here — we stub _request to return whatever
the test specifies and assert the runner classifies each check
correctly. Live audit integration lives under tests/security/.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.security.security_audit import (  # noqa: E402
    AuditTarget,
    SecurityAuditRunner,
    _HttpResponse,
)


GOOD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy":
        "default-src 'self'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class _StubRunner(SecurityAuditRunner):
    """Patches _request so the harness is driven by a fixture map
    ``(method, path) -> _HttpResponse``. Any unmatched probe returns
    a 500 so surprise paths surface as failures."""

    def __init__(self, target: AuditTarget, fixture: dict) -> None:
        super().__init__(target)
        self._fixture = fixture

    def _request(self, method, path, *, body=None, auth="",
                 content_type="", extra_headers=None) -> _HttpResponse:
        resp = self._fixture.get((method, path))
        if resp is None:
            return _HttpResponse(
                status=500, body=f"no fixture for {method} {path}",
                headers={},
            )
        return resp


class HarnessDecisionTests(unittest.TestCase):
    def _target(self, **kw) -> AuditTarget:
        defaults = {
            "base_url": "http://test",
            "admin_user": "admin",
            "admin_pass": "ci-pass-Strong1!",
            "public_paths": ["/healthz"],
            "sensitive_paths": ["/api/users", "/metrics"],
            "mutating_paths": ["/api/mutate"],
            "webhook_post_paths": ["/webhooks"],
        }
        defaults.update(kw)
        return AuditTarget(**defaults)

    def _good_fixture(self) -> dict:
        """A fully-compliant service: public 200, sensitive 401,
        headers everywhere, webhook rejects private IPs, etc."""
        ok_public = _HttpResponse(200, "ok", dict(GOOD_HEADERS))
        sensitive_unauth = _HttpResponse(401, "", dict(GOOD_HEADERS))
        sensitive_auth = _HttpResponse(
            200, '{"users":[]}', dict(GOOD_HEADERS),
        )
        webhook_reject = _HttpResponse(
            400, '{"error": "blocked private IP"}', dict(GOOD_HEADERS),
        )
        return {
            ("GET", "/healthz"): ok_public,
            ("GET", "/api/users"): sensitive_unauth,
            ("GET", "/metrics"): sensitive_unauth,
            # /api/users served auth'd
            # SecurityAuditRunner will also probe these paths with auth;
            # stub the authenticated read as 200 by matching the same
            # (method, path) key — _request doesn't differentiate, so
            # we wire sensitive GETs to return 200 and rely on the
            # wrong-creds check path using override user/pass that the
            # stub ignores. Keep it simple: always return 200 for
            # authenticated GETs.
            ("GET", "/api/users/"): sensitive_auth,
            ("POST", "/api/mutate"): _HttpResponse(
                429, '{"error":"rate"}', dict(GOOD_HEADERS),
            ),
            ("POST", "/webhooks"): webhook_reject,
        }

    def test_passing_service_passes_baseline(self):
        target = self._target()
        fixture = self._good_fixture()
        # Overwrite sensitive GETs to return 200 (authenticated read ok).
        fixture[("GET", "/api/users")] = _HttpResponse(
            200, '{"users":[]}', dict(GOOD_HEADERS),
        )
        runner = _StubRunner(target, fixture)
        results = runner.run_all()
        by_name = {r.check: r for r in results}
        # These checks are ones a good service should pass outright.
        for check in (
            "public_endpoints_allow_unauth",
            "security_headers",
            "hsts_value",
            "csp_default_src",
            "webhook_ssrf_block",
        ):
            self.assertEqual(by_name[check].status, "pass",
                             f"{check}: {by_name[check].detail}")

    def test_missing_security_headers_fails(self):
        target = self._target()
        fixture = self._good_fixture()
        # Strip headers from every response.
        for k, resp in list(fixture.items()):
            fixture[k] = _HttpResponse(
                status=resp.status, body=resp.body, headers={},
            )
        fixture[("GET", "/api/users")] = _HttpResponse(
            200, '{"users":[]}', {},
        )
        runner = _StubRunner(target, fixture)
        results = runner.run_all()
        by_name = {r.check: r for r in results}
        self.assertEqual(by_name["security_headers"].status, "fail")
        self.assertIn("missing:", by_name["security_headers"].detail)

    def test_leaking_password_in_body_fails(self):
        target = self._target()
        fixture = self._good_fixture()
        # Sensitive endpoint returns body with a real-looking password
        # field — e.g. the /api/keys regression we fixed.
        fixture[("GET", "/api/users")] = _HttpResponse(
            200,
            '{"admin": {"username": "admin", "password": "mypass"}}',
            dict(GOOD_HEADERS),
        )
        runner = _StubRunner(target, fixture)
        results = runner.run_all()
        by_name = {r.check: r for r in results}
        self.assertEqual(by_name["no_secret_in_errors"].status, "fail")

    def test_webhook_accepting_private_ip_fails(self):
        target = self._target()
        fixture = self._good_fixture()
        # Service accepts private-IP webhook (SSRF regression).
        fixture[("POST", "/webhooks")] = _HttpResponse(
            200, '{"webhook_urls":[]}', dict(GOOD_HEADERS),
        )
        runner = _StubRunner(target, fixture)
        results = runner.run_all()
        by_name = {r.check: r for r in results}
        self.assertEqual(by_name["webhook_ssrf_block"].status, "fail")


if __name__ == "__main__":
    unittest.main()
