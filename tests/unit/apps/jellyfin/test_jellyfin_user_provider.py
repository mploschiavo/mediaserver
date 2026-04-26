"""Tests for JellyfinApiProvider."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.user_provider import (  # noqa: E402
    JellyfinApiProvider, JellyfinProviderError,
)


def _mock_http(responses: dict):
    """Responses keyed by (method, path). JellyfinApiProvider now
    appends ``?api_key=X`` to every path (Jellyfin 10.11 rejects the
    shared HttpClient's X-Api-Key header), so the mock strips the
    query string before lookup."""
    def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
        bare_path = str(path).split("?", 1)[0]
        key = (method, bare_path)
        out = responses.get(key)
        if out is None:
            return (404, None, "")
        return out
    client = MagicMock()
    client.request.side_effect = _req
    return client


class JellyfinApiProviderTests(unittest.TestCase):
    def _provider(self, api_key="k1", client=None) -> JellyfinApiProvider:
        return JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key=api_key, http_client=client,
        )

    def test_create_user_happy_path(self):
        client = _mock_http({
            ("POST", "/Users/New"): (200, {"Id": "user-abc", "Name": "jane"}, ""),
            ("POST", "/Users/user-abc/Policy"): (204, None, ""),
        })
        p = self._provider(client=client)
        ext = p.create_user(
            username="jane", email="jane@x", display_name="Jane",
            password="pw", groups=[], policy={"IsAdministrator": False},
        )
        self.assertEqual(ext.external_id, "user-abc")

    def test_create_user_without_api_key_raises(self):
        p = self._provider(api_key="", client=_mock_http({}))
        with self.assertRaises(JellyfinProviderError):
            p.create_user(username="a", email="a@x", display_name="A",
                          password="pw", groups=[])

    def test_create_failure_status_raises(self):
        client = _mock_http({("POST", "/Users/New"): (400, None, "bad req")})
        p = self._provider(client=client)
        with self.assertRaises(JellyfinProviderError):
            p.create_user(username="a", email="a@x", display_name="A",
                          password="pw", groups=[])

    def test_set_password(self):
        client = _mock_http({("POST", "/Users/user-abc/Password"): (204, None, "")})
        p = self._provider(client=client)
        p.set_password("user-abc", "newpw")
        args_list = client.request.call_args_list
        payload = args_list[0].kwargs.get("payload")
        self.assertEqual(payload["NewPw"], "newpw")

    def test_api_key_flows_via_query_param_not_xapikey_header(self):
        """Regression for 2026-04-19: the shared HttpClient sends
        api_key as an ``X-Api-Key`` header, but Jellyfin 10.11+
        returns 401 for that header — it wants either
        ``X-Emby-Token`` or a ``?api_key=`` query parameter.

        Without this the dashboard's create-user flow silently
        fails at the Jellyfin secondary step ('status=401'),
        leaving the user unable to log into Jellyfin."""
        client = _mock_http({("GET", "/System/Info"): (200, {}, "")})
        p = self._provider(api_key="secret-key-123", client=client)
        p.health_check()
        call = client.request.call_args
        # The bare path argument must carry api_key as a query
        # parameter. Assert on the positional path arg (index 1).
        positional_path = call.args[1] if len(call.args) > 1 else ""
        self.assertIn(
            "api_key=secret-key-123", positional_path,
            "Jellyfin auth regressed: api_key must flow through "
            f"the path/query, not a header. Got path={positional_path!r}.",
        )
        # And the api_key KWARG (which would trigger the wrong
        # X-Api-Key header) must NOT be set.
        self.assertFalse(
            call.kwargs.get("api_key"),
            "api_key kwarg is set — this triggers X-Api-Key "
            "which Jellyfin 10.11 rejects with 401.",
        )

    def test_delete_user_handles_404_as_ok(self):
        client = _mock_http({("DELETE", "/Users/user-abc"): (404, None, "")})
        p = self._provider(client=client)
        p.delete_user("user-abc")

    def test_list_users_returns_empty_on_error(self):
        client = MagicMock()
        client.request.side_effect = RuntimeError("net down")
        p = self._provider(client=client)
        self.assertEqual(p.list_users(), [])

    def test_revoke_sessions_kills_only_this_users_sessions(self):
        client = _mock_http({
            ("GET", "/Sessions"): (200, [
                {"Id": "s1", "UserId": "user-abc"},
                {"Id": "s2", "UserId": "other"},
                {"Id": "s3", "UserId": "user-abc"},
            ], ""),
            ("DELETE", "/Sessions/s1"): (204, None, ""),
            ("DELETE", "/Sessions/s3"): (204, None, ""),
        })
        p = self._provider(client=client)
        p.revoke_sessions("user-abc")
        deletes = [c.args[1] for c in client.request.call_args_list
                    if c.kwargs.get("method") == "DELETE"]
        self.assertEqual(set(deletes), {"/Sessions/s1", "/Sessions/s3"})

    def test_revoke_sessions_tolerates_list_failure(self):
        client = MagicMock()
        client.request.side_effect = RuntimeError("net down")
        p = self._provider(client=client)
        p.revoke_sessions("user-abc")  # must not raise


if __name__ == "__main__":
    unittest.main()
