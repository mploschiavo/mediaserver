"""Unit tests for JellyseerrSessionProvider."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.security.providers.jellyseerr_session_provider import (  # noqa: E402
    JellyseerrSessionProvider,
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


class JellyseerrSessionProviderTests(unittest.TestCase):

    # ---- probe / construction -----------------------------------------

    def test_no_api_key_marks_unavailable(self):
        http = _FakeHttp([])
        p = JellyseerrSessionProvider(
            base_url="http://j:5055", api_key="", http_client=http,
        )
        self.assertFalse(p.available)
        self.assertEqual(http.calls, [])

    def test_probe_connection_refused_marks_unavailable(self):
        http = _FakeHttp([RuntimeError("ECONNREFUSED")])
        p = JellyseerrSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertFalse(p.available)

    def test_probe_200_marks_available(self):
        http = _FakeHttp([(200, {"version": "2.0"}, "")])
        p = JellyseerrSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertTrue(p.available)

    # ---- list_sessions ------------------------------------------------

    def test_list_sessions_always_empty(self):
        http = _FakeHttp([(200, {"version": "2.0"}, "")])
        p = JellyseerrSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.list_sessions("alice"), [])
        self.assertEqual(p.list_sessions(""), [])

    def test_revoke_session_returns_false(self):
        http = _FakeHttp([(200, {"version": "2.0"}, "")])
        p = JellyseerrSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertFalse(p.revoke_session("alice", "any"))

    def test_revoke_sessions_returns_zero(self):
        http = _FakeHttp([(200, {"version": "2.0"}, "")])
        p = JellyseerrSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.revoke_sessions("alice"), 0)

    def test_no_extra_calls_after_probe(self):
        # No HTTP calls happen after probe — confirm that list/revoke
        # are total no-ops.
        http = _FakeHttp([(200, {}, "")])
        p = JellyseerrSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        before = len(http.calls)
        p.list_sessions("alice")
        p.revoke_session("alice", "x")
        p.revoke_sessions("alice")
        self.assertEqual(len(http.calls), before)

    # ---- from_env -----------------------------------------------------

    def test_from_env_missing_api_key_returns_none(self):
        p = from_env(env={"JELLYSEERR_URL": "http://j"}, http_client=None)
        self.assertIsNone(p)

    def test_from_env_with_key_constructs(self):
        http = _FakeHttp([(200, {}, "")])
        p = from_env(
            env={"JELLYSEERR_URL": "http://j", "JELLYSEERR_API_KEY": "k"},
            http_client=http,
        )
        self.assertIsNotNone(p)
        self.assertEqual(http.calls[0]["path"], "/api/v1/status")


if __name__ == "__main__":
    unittest.main()
