"""Unit tests for AutheliaSessionProvider."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.security.providers.authelia_session_provider import (  # noqa: E402
    AutheliaSessionProvider,
    from_env,
)


class _FakeHttp:
    """Records (base, path, method, api_key) and returns scripted replies."""

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


class AutheliaSessionProviderTests(unittest.TestCase):

    # ---- construction & probe -----------------------------------------

    def test_probe_404_marks_unavailable(self):
        http = _FakeHttp([(404, None, "")])
        p = AutheliaSessionProvider(
            base_url="http://authelia:9091", api_key="k",
            http_client=http,
        )
        self.assertFalse(p.available)
        self.assertEqual(p.list_sessions("alice"), [])

    def test_probe_connection_refused_marks_unavailable(self):
        http = _FakeHttp([RuntimeError("ECONNREFUSED")])
        p = AutheliaSessionProvider(
            base_url="http://authelia:9091", api_key="k",
            http_client=http,
        )
        self.assertFalse(p.available)

    def test_probe_returns_non_list_marks_unavailable(self):
        http = _FakeHttp([(200, {"oops": True}, "{}")])
        p = AutheliaSessionProvider(
            base_url="http://authelia:9091", api_key="k",
            http_client=http,
        )
        self.assertFalse(p.available)

    def test_probe_200_list_marks_available(self):
        http = _FakeHttp([(200, [], "[]")])
        p = AutheliaSessionProvider(
            base_url="http://authelia:9091", api_key="k",
            http_client=http,
        )
        self.assertTrue(p.available)

    def test_probe_disabled_skips_calls(self):
        http = _FakeHttp([])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k",
            http_client=http, probe_on_init=False,
        )
        self.assertFalse(p.available)
        self.assertEqual(http.calls, [])

    # ---- list_sessions ------------------------------------------------

    def test_list_sessions_filters_by_username(self):
        rows = [
            {"id": "s1", "username": "alice", "ip": "1.1.1.1",
             "user_agent": "Chrome", "last_activity": "2026-04-24T01:00Z"},
            {"id": "s2", "username": "bob", "ip": "2.2.2.2"},
            {"id": "s3", "username": "alice", "ip": "1.1.1.2"},
        ]
        # First call is probe, second call is list_sessions.
        http = _FakeHttp([(200, rows, ""), (200, rows, "")])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        out = p.list_sessions("alice")
        self.assertEqual([s.session_id for s in out], ["s1", "s3"])
        self.assertEqual(out[0].ip, "1.1.1.1")
        self.assertEqual(out[0].client, "Chrome")

    def test_list_sessions_unknown_user_returns_empty(self):
        http = _FakeHttp([
            (200, [{"id": "s1", "username": "alice"}], ""),
            (200, [{"id": "s1", "username": "alice"}], ""),
        ])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.list_sessions("nobody"), [])

    def test_list_sessions_empty_username_returns_empty(self):
        http = _FakeHttp([(200, [], "")])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.list_sessions(""), [])

    def test_list_sessions_when_unavailable_returns_empty(self):
        http = _FakeHttp([(404, None, "")])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        self.assertEqual(p.list_sessions("alice"), [])

    # ---- revoke -------------------------------------------------------

    def test_revoke_session_calls_correct_url(self):
        # Probe (200), revoke_one (200).
        http = _FakeHttp([(200, [], ""), (200, None, "")])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        p.revoke_session("alice", "sess-xyz")
        revoke_call = http.calls[-1]
        self.assertEqual(revoke_call["method"], "POST")
        self.assertEqual(
            revoke_call["path"], "/api/sessions/sess-xyz/revoke",
        )

    def test_revoke_session_when_unavailable_is_noop(self):
        http = _FakeHttp([(404, None, "")])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        # Only the probe should have been called.
        p.revoke_session("alice", "sess-xyz")
        self.assertEqual(len(http.calls), 1)

    def test_revoke_sessions_iterates(self):
        rows = [
            {"id": "s1", "username": "alice"},
            {"id": "s2", "username": "alice"},
        ]
        # Probe, list_sessions, then revoke s1, revoke s2.
        http = _FakeHttp([
            (200, rows, ""),
            (200, rows, ""),
            (200, None, ""),
            (200, None, ""),
        ])
        p = AutheliaSessionProvider(
            base_url="http://x", api_key="k", http_client=http,
        )
        p.revoke_sessions("alice")
        revoke_calls = [c for c in http.calls if c["method"] == "POST"]
        self.assertEqual(len(revoke_calls), 2)
        paths = sorted(c["path"] for c in revoke_calls)
        self.assertEqual(paths, [
            "/api/sessions/s1/revoke", "/api/sessions/s2/revoke",
        ])

    # ---- from_env -----------------------------------------------------

    def test_from_env_defaults(self):
        http = _FakeHttp([(404, None, "")])
        p = from_env(env={}, http_client=http)
        self.assertIsNotNone(p)
        # Default URL was used in the probe.
        self.assertEqual(http.calls[0]["base"], "http://authelia:9091")


if __name__ == "__main__":
    unittest.main()
