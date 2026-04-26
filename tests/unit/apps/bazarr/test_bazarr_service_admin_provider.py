"""Tests for BazarrServiceAdminProvider."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.bazarr.service_admin_provider import (  # noqa: E402
    BazarrServiceAdminProvider,
    BazarrServiceAdminProviderError,
)


def _http(responses: dict) -> MagicMock:
    def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
        return responses.get((method, path), (404, None, ""))
    c = MagicMock()
    c.request.side_effect = _req
    return c


class BazarrServiceAdminProviderTests(unittest.TestCase):
    def _provider(self, client, api_key="k"):
        return BazarrServiceAdminProvider(
            base_url="http://bazarr:6767", api_key=api_key,
            http_client=client,
        )

    def test_set_password_posts_form_settings(self):
        client = _http({("POST", "/api/system/settings"): (200, None, "")})
        p = self._provider(client)
        p.set_admin_password("new-pw")
        call = [c for c in client.request.call_args_list
                 if c.kwargs.get("method") == "POST"][0]
        payload = call.kwargs["payload"]
        self.assertEqual(payload["auth"]["type"], "form")
        self.assertEqual(payload["auth"]["username"], "admin")
        self.assertEqual(payload["auth"]["password"], "new-pw")

    def test_302_is_accepted_as_success(self):
        """Bazarr returns 302 on successful settings write."""
        client = _http({("POST", "/api/system/settings"): (302, None, "")})
        p = self._provider(client)
        p.set_admin_password("new-pw")  # must not raise

    def test_missing_api_key_raises(self):
        p = self._provider(_http({}), api_key="")
        with self.assertRaises(BazarrServiceAdminProviderError):
            p.set_admin_password("x")

    def test_non_success_status_raises(self):
        client = _http({("POST", "/api/system/settings"): (500, None, "err")})
        p = self._provider(client)
        with self.assertRaises(BazarrServiceAdminProviderError):
            p.set_admin_password("x")

    def test_health_check(self):
        client = _http({("GET", "/api/system/status"): (200, {"ok": 1}, "")})
        p = self._provider(client)
        self.assertTrue(p.health_check().ok)


if __name__ == "__main__":
    unittest.main()
