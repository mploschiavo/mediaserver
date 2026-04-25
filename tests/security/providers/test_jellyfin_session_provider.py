"""Unit tests for JellyfinSessionProvider."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.security.providers.jellyfin_session_provider import (  # noqa: E402
    JellyfinSessionProvider,
    from_env,
)


class _FakeHttp:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def request(self, base_url, path, *, api_key=None, method="GET",
                payload=None, timeout=20):
        self.calls.append({
            "base": base_url, "path": path, "method": method,
            "api_key": api_key, "payload": payload, "timeout": timeout,
        })
        if not self.replies:
            raise RuntimeError("connection refused")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


_SESSION_ROWS = [
    {
        "Id": "j-1", "UserName": "alice", "RemoteEndPoint": "10.0.0.5",
        "Client": "Jellyfin Web", "ApplicationVersion": "10.11.0",
        "DeviceName": "Chrome", "LastActivityDate": "2026-04-24T05:00:00Z",
    },
    {
        "Id": "j-2", "UserName": "bob", "RemoteEndPoint": "10.0.0.6",
        "Client": "Android TV", "ApplicationVersion": "2.5",
        "DeviceName": "Living Room", "LastActivityDate": "2026-04-24T04:00Z",
    },
    {
        "Id": "j-3", "UserName": "alice", "RemoteEndPoint": "10.0.0.7",
        "Client": "iPhone",
    },
]


class JellyfinSessionProviderTests(unittest.TestCase):

    # ---- probe / construction -----------------------------------------

    def test_no_api_key_marks_unavailable(self):
        http = _FakeHttp([])
        p = JellyfinSessionProvider(
            base_url="http://jellyfin:8096", api_key="",
            http_client=http,
        )
        self.assertFalse(p.available)
        self.assertEqual(http.calls, [])

    def test_probe_404_marks_unavailable(self):
        http = _FakeHttp([(404, None, "")])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertFalse(p.available)

    def test_probe_connection_refused_marks_unavailable(self):
        http = _FakeHttp([RuntimeError("ECONNREFUSED")])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertFalse(p.available)

    def test_probe_200_marks_available(self):
        http = _FakeHttp([(200, [], "[]")])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertTrue(p.available)

    # ---- list_sessions ------------------------------------------------

    def test_list_sessions_filters_by_username(self):
        http = _FakeHttp([
            (200, _SESSION_ROWS, ""),  # probe
            (200, _SESSION_ROWS, ""),  # list_sessions
        ])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        out = p.list_sessions("alice")
        self.assertEqual([s.session_id for s in out], ["j-1", "j-3"])
        self.assertEqual(out[0].client, "Jellyfin Web 10.11.0")
        self.assertEqual(out[0].device, "Chrome")
        self.assertEqual(out[0].ip, "10.0.0.5")
        self.assertEqual(out[0].last_activity, "2026-04-24T05:00:00Z")

    def test_list_sessions_unknown_user_empty(self):
        http = _FakeHttp([
            (200, _SESSION_ROWS, ""),
            (200, _SESSION_ROWS, ""),
        ])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.list_sessions("nobody"), [])

    def test_list_sessions_unavailable_empty(self):
        http = _FakeHttp([(503, None, "")])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.list_sessions("alice"), [])

    # ---- revoke -------------------------------------------------------

    def test_revoke_session_uses_delete_method(self):
        http = _FakeHttp([
            (200, [], ""),       # probe
            (204, None, ""),     # delete
        ])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        ok = p.revoke_session("alice", "j-1")
        self.assertTrue(ok)
        delete_call = http.calls[-1]
        self.assertEqual(delete_call["method"], "DELETE")
        self.assertEqual(delete_call["path"], "/Sessions/j-1")
        self.assertEqual(delete_call["api_key"], "k")

    def test_revoke_session_404_treated_as_success(self):
        http = _FakeHttp([
            (200, [], ""),
            (404, None, ""),
        ])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertTrue(p.revoke_session("alice", "missing"))

    def test_revoke_sessions_iterates_and_counts(self):
        http = _FakeHttp([
            (200, _SESSION_ROWS, ""),  # probe
            (200, _SESSION_ROWS, ""),  # list
            (204, None, ""),           # delete j-1
            (204, None, ""),           # delete j-3
        ])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        n = p.revoke_sessions("alice")
        self.assertEqual(n, 2)
        deletes = [c for c in http.calls if c["method"] == "DELETE"]
        self.assertEqual(len(deletes), 2)
        self.assertEqual(
            sorted(c["path"] for c in deletes),
            ["/Sessions/j-1", "/Sessions/j-3"],
        )

    def test_revoke_sessions_unknown_user_returns_zero(self):
        http = _FakeHttp([
            (200, _SESSION_ROWS, ""),
            (200, _SESSION_ROWS, ""),
        ])
        p = JellyfinSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.revoke_sessions("nobody"), 0)

    # ---- from_env -----------------------------------------------------

    def test_from_env_missing_api_key_returns_none(self):
        p = from_env(env={"JELLYFIN_URL": "http://j"}, http_client=None)
        self.assertIsNone(p)

    def test_from_env_with_key_constructs(self):
        http = _FakeHttp([(404, None, "")])
        p = from_env(
            env={"JELLYFIN_URL": "http://j", "JELLYFIN_API_KEY": "k"},
            http_client=http,
        )
        self.assertIsNotNone(p)
        self.assertEqual(http.calls[0]["base"], "http://j")
        self.assertEqual(http.calls[0]["api_key"], "k")


if __name__ == "__main__":
    unittest.main()
