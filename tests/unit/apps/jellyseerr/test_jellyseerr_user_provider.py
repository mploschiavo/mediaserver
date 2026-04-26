"""Tests for JellyseerrApiProvider — OIDC-deferred provisioning and
post-first-login permission sync.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyseerr.user_provider import (  # noqa: E402
    JellyseerrApiProvider, JellyseerrProviderError,
)


def _http(responses: dict):
    def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
        return responses.get((method, path), (404, None, ""))
    c = MagicMock()
    c.request.side_effect = _req
    return c


class JellyseerrProviderTests(unittest.TestCase):
    def _provider(self, client=None) -> JellyseerrApiProvider:
        return JellyseerrApiProvider(
            base_url="http://jellyseerr:5055", api_key="key",
            http_client=client or _http({}),
        )

    # --- deferred create ---

    def test_create_user_is_deferred_not_an_http_call(self):
        client = _http({})
        p = self._provider(client)
        ext = p.create_user(
            username="jane", email="j@x", display_name="J",
            password="pw", groups=[],
        )
        self.assertEqual(ext.external_id, "")
        self.assertEqual(ext.extra.get("deferred"), "oidc_first_login")
        # Explicitly: no HTTP calls during create
        client.request.assert_not_called()

    def test_capabilities_signal_oidc_deferral(self):
        p = self._provider()
        self.assertTrue(p.capabilities.auto_provisions_on_login)
        self.assertFalse(p.capabilities.supports_password)
        self.assertFalse(p.capabilities.source_of_truth)

    # --- list_users picks up post-login records ---

    def test_list_users_handles_results_envelope(self):
        client = _http({("GET", "/api/v1/user"): (200, {
            "results": [
                {"id": 1, "jellyfinUsername": "jane", "email": "j@x"},
                {"id": 2, "username": "admin", "email": "a@x"},
            ],
        }, "")})
        p = self._provider(client)
        users = p.list_users()
        self.assertEqual(len(users), 2)
        self.assertEqual(users[0].username, "jane")
        self.assertEqual(users[0].external_id, "1")

    def test_list_users_handles_bare_list(self):
        client = _http({("GET", "/api/v1/user"): (200, [
            {"id": 5, "username": "kid"},
        ], "")})
        p = self._provider(client)
        users = p.list_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].external_id, "5")

    def test_list_users_empty_on_error(self):
        client = _http({("GET", "/api/v1/user"): (500, None, "err")})
        p = self._provider(client)
        self.assertEqual(p.list_users(), [])

    # --- update_user applies role permissions + quotas ---

    def test_update_user_patches_permissions_and_quotas(self):
        client = _http({("PUT", "/api/v1/user/42"): (200, {}, "")})
        p = self._provider(client)
        p.update_user("42", policy={
            "permissions": 32,
            "request_quota": {"movies": 10, "tv": 5},
        })
        put_call = [c for c in client.request.call_args_list
                     if c.kwargs.get("method") == "PUT"][0]
        body = put_call.kwargs["payload"]
        self.assertEqual(body["permissions"], 32)
        self.assertEqual(body["movieQuotaLimit"], 10)
        self.assertEqual(body["movieQuotaDays"], 7)
        self.assertEqual(body["tvQuotaLimit"], 5)

    def test_update_without_external_id_is_noop_not_error(self):
        """Pre-first-login: user has no Jellyseerr ID yet. Skip gracefully."""
        client = _http({})
        p = self._provider(client)
        p.update_user("", policy={"permissions": 2})
        client.request.assert_not_called()

    def test_update_without_quotas_only_sets_permissions(self):
        client = _http({("PUT", "/api/v1/user/7"): (200, {}, "")})
        p = self._provider(client)
        p.update_user("7", policy={"permissions": 0})
        body = [c for c in client.request.call_args_list
                 if c.kwargs.get("method") == "PUT"][0].kwargs["payload"]
        self.assertEqual(body, {"permissions": 0})

    def test_update_rejected_by_api_raises(self):
        client = _http({("PUT", "/api/v1/user/3"): (400, None, "bad perms")})
        p = self._provider(client)
        with self.assertRaises(JellyseerrProviderError):
            p.update_user("3", policy={"permissions": 32})

    # --- set_password is a no-op (OIDC-federated) ---

    def test_set_password_is_noop(self):
        client = _http({})
        p = self._provider(client)
        p.set_password("1", "anything")
        client.request.assert_not_called()

    # --- delete ---

    def test_delete_user_calls_api(self):
        client = _http({("DELETE", "/api/v1/user/8"): (204, None, "")})
        p = self._provider(client)
        p.delete_user("8")
        delete_call = [c for c in client.request.call_args_list
                        if c.kwargs.get("method") == "DELETE"]
        self.assertEqual(len(delete_call), 1)


if __name__ == "__main__":
    unittest.main()
