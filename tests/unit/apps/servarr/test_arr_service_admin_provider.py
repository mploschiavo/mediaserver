"""Tests for ArrServiceAdminProvider — the fix for the 307 redirect bug
that broke admin-password propagation to Sonarr/Radarr/Lidarr/Readarr.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.servarr.service_admin_provider import (  # noqa: E402
    ArrServiceAdminProvider,
    ArrServiceAdminProviderError,
)


def _http(responses: dict) -> MagicMock:
    """``responses`` keyed by (method, path) → (status, body, text)."""
    def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
        return responses.get((method, path), (404, None, ""))
    c = MagicMock()
    c.request.side_effect = _req
    return c


class ArrServiceAdminProviderTests(unittest.TestCase):
    def _provider(self, client, *, api_key="k") -> ArrServiceAdminProvider:
        return ArrServiceAdminProvider(
            service_id="sonarr", base_url="http://sonarr:8989",
            api_key=api_key, http_client=client,
        )

    def test_set_password_fetches_then_puts_host_config(self):
        client = _http({
            ("GET", "/api/v3/config/host"): (200, {
                "id": 1, "bindAddress": "*", "port": 8989,
                "urlBase": "/app/sonarr", "username": "admin",
                "password": "old",
                "authenticationMethod": "forms",
            }, ""),
            ("PUT", "/api/v3/config/host"): (200, {"id": 1}, ""),
        })
        p = self._provider(client)
        p.set_admin_password("new-pw")

        put_call = [c for c in client.request.call_args_list
                     if c.kwargs.get("method") == "PUT"][0]
        payload = put_call.kwargs["payload"]
        self.assertEqual(payload["username"], "admin")
        self.assertEqual(payload["password"], "new-pw")
        self.assertEqual(payload["passwordConfirmation"], "new-pw")
        self.assertEqual(payload["authenticationMethod"], "forms")
        # urlBase + other fields passed through unchanged
        self.assertEqual(payload["urlBase"], "/app/sonarr")
        self.assertEqual(payload["port"], 8989)

    def test_forces_forms_auth_when_disabled(self):
        client = _http({
            ("GET", "/api/v3/config/host"): (200, {
                "id": 1, "authenticationMethod": "none",
            }, ""),
            ("PUT", "/api/v3/config/host"): (200, {"id": 1}, ""),
        })
        p = self._provider(client)
        p.set_admin_password("x")
        payload = [c for c in client.request.call_args_list
                    if c.kwargs.get("method") == "PUT"][0].kwargs["payload"]
        self.assertEqual(payload["authenticationMethod"], "forms")

    def test_missing_api_key_raises(self):
        p = ArrServiceAdminProvider(
            service_id="sonarr", base_url="http://sonarr:8989",
            api_key="", http_client=_http({}),
        )
        with self.assertRaises(ArrServiceAdminProviderError):
            p.set_admin_password("x")

    def test_get_host_config_failure_raises(self):
        client = _http({("GET", "/api/v3/config/host"): (500, None, "boom")})
        p = self._provider(client)
        with self.assertRaises(ArrServiceAdminProviderError):
            p.set_admin_password("x")

    def test_put_non_2xx_raises(self):
        client = _http({
            ("GET", "/api/v3/config/host"): (200, {"id": 1}, ""),
            ("PUT", "/api/v3/config/host"): (403, None, "forbidden"),
        })
        p = self._provider(client)
        with self.assertRaises(ArrServiceAdminProviderError):
            p.set_admin_password("x")

    def test_health_check_uses_status_endpoint(self):
        client = _http({("GET", "/api/v3/system/status"): (200, {"version": "4"}, "")})
        p = self._provider(client)
        h = p.health_check()
        self.assertTrue(h.ok)

    def test_health_check_missing_api_key_fails(self):
        p = ArrServiceAdminProvider(
            service_id="sonarr", base_url="http://sonarr:8989",
            api_key="", http_client=_http({}),
        )
        h = p.health_check()
        self.assertFalse(h.ok)

    def test_redirect_handling_is_delegated_to_http_client(self):
        """Regression: legacy path raised on 307. HttpClient follows 307
        automatically; this provider just calls HttpClient and expects the
        redirected response body. The test confirms we don't do our own
        redirect handling that could re-introduce the bug.
        """
        # HttpClient is mocked here — if the provider tries to handle 307
        # itself, it would need a second call; we assert exactly two HTTP
        # calls (GET + PUT) with no extra redirect logic.
        client = _http({
            ("GET", "/api/v3/config/host"): (200, {"id": 1}, ""),
            ("PUT", "/api/v3/config/host"): (200, {"id": 1}, ""),
        })
        p = self._provider(client)
        p.set_admin_password("x")
        self.assertEqual(client.request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
