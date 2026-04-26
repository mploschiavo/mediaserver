"""Tests for the Jellyfin session-visibility mixin.

Covers the extensions added in ``services.apps.jellyfin.visibility_mixin``:
per-session revoke, account-state (IsDisabled via Policy), MFA (always
none), and API-token list + revoke via ``/Auth/Keys``.

Uses the same in-memory HTTP mock as ``test_jellyfin_user_provider``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.visibility_protocols import (  # noqa: E402
    APIToken,
    APITokenProvider,
    AccountStateProvider,
    MFAState,
    MFAStateProvider,
    SessionAdminProvider,
)
from media_stack.services.apps.jellyfin.user_provider import (  # noqa: E402
    JellyfinApiProvider,
    JellyfinProviderError,
)


def _mock_http(responses: dict):
    """Return (client, request_log). Responses keyed by (method, path).

    Jellyfin's ``?api_key=`` query param is stripped before lookup,
    matching the shape used by the existing provider tests.
    """
    log: list[tuple[str, str, dict]] = []

    def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
        bare_path = str(path).split("?", 1)[0]
        log.append((method, bare_path, payload or {}))
        out = responses.get((method, bare_path))
        if out is None:
            return (404, None, "")
        return out

    client = MagicMock()
    client.request.side_effect = _req
    return client, log


class JellyfinProtocolConformanceTests(unittest.TestCase):
    """A configured Jellyfin provider must satisfy every optional
    protocol — the session-aggregator relies on ``isinstance`` probing
    to decide which providers contribute which data."""

    def _provider(self) -> JellyfinApiProvider:
        client, _ = _mock_http({})
        return JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )

    def test_satisfies_session_admin(self) -> None:
        self.assertIsInstance(self._provider(), SessionAdminProvider)

    def test_satisfies_account_state(self) -> None:
        self.assertIsInstance(self._provider(), AccountStateProvider)

    def test_satisfies_mfa_state(self) -> None:
        self.assertIsInstance(self._provider(), MFAStateProvider)

    def test_satisfies_api_token(self) -> None:
        self.assertIsInstance(self._provider(), APITokenProvider)


class JellyfinRevokeSessionTests(unittest.TestCase):

    def _provider(self, responses: dict):
        client, log = _mock_http(responses)
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        return p, log

    def test_revoke_owned_session_issues_delete(self) -> None:
        sessions_list = [
            {"Id": "s-1", "UserId": "u-alice", "DeviceName": "Shield TV"},
            {"Id": "s-2", "UserId": "u-bob", "DeviceName": "iPhone"},
        ]
        p, log = self._provider({
            ("GET", "/Sessions"): (200, sessions_list, ""),
            ("DELETE", "/Sessions/s-1"): (204, None, ""),
        })
        p.revoke_session("u-alice", "s-1")
        methods_paths = [(m, path) for m, path, _ in log]
        self.assertIn(("DELETE", "/Sessions/s-1"), methods_paths)

    def test_revoke_foreign_session_is_noop(self) -> None:
        """Safety: passing someone else's session_id must not DELETE."""
        sessions_list = [
            {"Id": "s-2", "UserId": "u-bob", "DeviceName": "iPhone"},
        ]
        p, log = self._provider({
            ("GET", "/Sessions"): (200, sessions_list, ""),
        })
        p.revoke_session("u-alice", "s-2")  # alice doesn't own s-2
        methods = [m for m, _, _ in log]
        self.assertNotIn("DELETE", methods)

    def test_revoke_unknown_session_is_noop(self) -> None:
        p, log = self._provider({
            ("GET", "/Sessions"): (200, [], ""),
        })
        p.revoke_session("u-alice", "s-404")
        methods = [m for m, _, _ in log]
        self.assertNotIn("DELETE", methods)

    def test_revoke_empty_inputs_noop(self) -> None:
        p, log = self._provider({})
        p.revoke_session("", "s-1")
        p.revoke_session("u-alice", "")
        self.assertEqual(log, [])

    def test_revoke_without_api_key_noop(self) -> None:
        client, log = _mock_http({})
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="", http_client=client,
        )
        p.revoke_session("u-alice", "s-1")
        self.assertEqual(log, [])

    def test_list_failure_swallowed(self) -> None:
        client = MagicMock()
        client.request.side_effect = RuntimeError("boom")
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        # Does not raise.
        p.revoke_session("u-alice", "s-1")


class JellyfinAccountStateTests(unittest.TestCase):

    def _user_doc(self, is_disabled: bool = False) -> dict:
        return {
            "Id": "u-alice",
            "Name": "alice",
            "Policy": {
                "IsAdministrator": False,
                "IsDisabled": is_disabled,
                "EnableAllFolders": True,
            },
        }

    def _provider(self, responses: dict):
        client, log = _mock_http(responses)
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        return p, log

    def test_disable_flips_flag_and_posts_full_policy(self) -> None:
        p, log = self._provider({
            ("GET", "/Users/u-alice"): (200, self._user_doc(False), ""),
            ("POST", "/Users/u-alice/Policy"): (204, None, ""),
        })
        p.disable_user("u-alice")
        posts = [entry for entry in log if entry[0] == "POST"]
        self.assertEqual(len(posts), 1)
        _, path, payload = posts[0]
        self.assertEqual(path, "/Users/u-alice/Policy")
        # The full policy must be posted (not a sparse merge) — other
        # fields must be preserved intact.
        self.assertTrue(payload["IsDisabled"])
        self.assertIn("IsAdministrator", payload)
        self.assertIn("EnableAllFolders", payload)
        self.assertFalse(payload["IsAdministrator"])
        self.assertTrue(payload["EnableAllFolders"])

    def test_enable_clears_flag(self) -> None:
        p, log = self._provider({
            ("GET", "/Users/u-alice"): (200, self._user_doc(True), ""),
            ("POST", "/Users/u-alice/Policy"): (204, None, ""),
        })
        p.enable_user("u-alice")
        posts = [entry for entry in log if entry[0] == "POST"]
        self.assertEqual(len(posts), 1)
        _, _, payload = posts[0]
        self.assertFalse(payload["IsDisabled"])

    def test_disable_is_idempotent_skips_write_when_already_set(self) -> None:
        p, log = self._provider({
            ("GET", "/Users/u-alice"): (200, self._user_doc(True), ""),
        })
        p.disable_user("u-alice")
        # No POST emitted — already disabled.
        posts = [entry for entry in log if entry[0] == "POST"]
        self.assertEqual(posts, [])

    def test_enable_is_idempotent(self) -> None:
        p, log = self._provider({
            ("GET", "/Users/u-alice"): (200, self._user_doc(False), ""),
        })
        p.enable_user("u-alice")
        posts = [entry for entry in log if entry[0] == "POST"]
        self.assertEqual(posts, [])

    def test_is_disabled_true(self) -> None:
        p, _ = self._provider({
            ("GET", "/Users/u-alice"): (200, self._user_doc(True), ""),
        })
        self.assertTrue(p.is_disabled("u-alice"))

    def test_is_disabled_false(self) -> None:
        p, _ = self._provider({
            ("GET", "/Users/u-alice"): (200, self._user_doc(False), ""),
        })
        self.assertFalse(p.is_disabled("u-alice"))

    def test_is_disabled_missing_user_returns_false(self) -> None:
        p, _ = self._provider({})  # every GET 404s
        self.assertFalse(p.is_disabled("u-ghost"))

    def test_disable_missing_user_raises(self) -> None:
        p, _ = self._provider({})  # every GET 404s
        with self.assertRaises(JellyfinProviderError):
            p.disable_user("u-ghost")

    def test_disable_empty_external_id_raises(self) -> None:
        p, _ = self._provider({})
        with self.assertRaises(JellyfinProviderError):
            p.disable_user("")

    def test_is_disabled_without_api_key_returns_false(self) -> None:
        client, _ = _mock_http({})
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="", http_client=client,
        )
        self.assertFalse(p.is_disabled("u-alice"))


class JellyfinMFAStateTests(unittest.TestCase):

    def test_always_returns_none(self) -> None:
        client, _ = _mock_http({})
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        state = p.mfa_state("u-alice")
        self.assertIsInstance(state, MFAState)
        self.assertFalse(state.enrolled)
        self.assertEqual(state.enrolled_methods, ())


class JellyfinAPITokenTests(unittest.TestCase):

    def _keys_items(self) -> list[dict]:
        return [
            {
                "AccessToken": "tok-alice-shield",
                "AppName": "Jellyfin for Android TV",
                "UserId": "u-alice",
                "UserName": "alice",
                "DateCreated": "2026-01-01T00:00:00Z",
                "DateLastActivity": "2026-04-20T00:00:00Z",
            },
            {
                "AccessToken": "tok-alice-iphone",
                "AppName": "Jellyfin Mobile",
                "UserId": "u-alice",
                "UserName": "alice",
                "DateCreated": "2026-02-01T00:00:00Z",
                "DateLastActivity": "",
            },
            {
                "AccessToken": "tok-bob",
                "AppName": "Jellyfin Web",
                "UserId": "u-bob",
                "UserName": "bob",
            },
        ]

    def _provider(self, keys_response):
        client, log = _mock_http({
            ("GET", "/Auth/Keys"): keys_response,
        })
        return (
            JellyfinApiProvider(
                base_url="http://jellyfin:8096",
                api_key="k1",
                http_client=client,
            ),
            log,
        )

    def test_list_filters_by_user(self) -> None:
        p, _ = self._provider((200, {"Items": self._keys_items()}, ""))
        tokens = p.list_api_tokens("u-alice")
        self.assertEqual(len(tokens), 2)
        self.assertEqual(
            {t.name for t in tokens},
            {"Jellyfin for Android TV", "Jellyfin Mobile"},
        )
        # Metadata populated; secret never duplicated into a separate field.
        alice_shield = [t for t in tokens if t.name.endswith("Android TV")][0]
        self.assertEqual(alice_shield.created_at, "2026-01-01T00:00:00Z")
        self.assertEqual(alice_shield.last_used_at, "2026-04-20T00:00:00Z")
        self.assertEqual(alice_shield.created_by, "alice")

    def test_list_tolerates_bare_list_payload(self) -> None:
        # Older Jellyfin returned a bare list — ``_extract_key_items``
        # normalises either shape.
        p, _ = self._provider((200, self._keys_items(), ""))
        tokens = p.list_api_tokens("u-alice")
        self.assertEqual(len(tokens), 2)

    def test_list_empty_when_no_api_key(self) -> None:
        client, _ = _mock_http({})
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="", http_client=client,
        )
        self.assertEqual(p.list_api_tokens("u-alice"), [])

    def test_list_empty_on_non_200(self) -> None:
        p, _ = self._provider((401, None, "unauth"))
        self.assertEqual(p.list_api_tokens("u-alice"), [])

    def test_list_empty_on_transport_error(self) -> None:
        client = MagicMock()
        client.request.side_effect = RuntimeError("boom")
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        self.assertEqual(p.list_api_tokens("u-alice"), [])

    def test_list_empty_user_id_returns_empty(self) -> None:
        p, _ = self._provider((200, {"Items": self._keys_items()}, ""))
        self.assertEqual(p.list_api_tokens(""), [])

    def test_list_returns_APIToken_dataclass(self) -> None:
        p, _ = self._provider((200, {"Items": self._keys_items()}, ""))
        tokens = p.list_api_tokens("u-alice")
        for t in tokens:
            self.assertIsInstance(t, APIToken)

    def test_revoke_owned_token_issues_delete(self) -> None:
        client, log = _mock_http({
            ("GET", "/Auth/Keys"): (200, {"Items": self._keys_items()}, ""),
            ("DELETE", "/Auth/Keys/tok-alice-shield"): (204, None, ""),
        })
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        p.revoke_api_token("u-alice", "tok-alice-shield")
        methods_paths = [(m, path) for m, path, _ in log]
        self.assertIn(("DELETE", "/Auth/Keys/tok-alice-shield"), methods_paths)

    def test_revoke_foreign_token_is_noop(self) -> None:
        client, log = _mock_http({
            ("GET", "/Auth/Keys"): (200, {"Items": self._keys_items()}, ""),
        })
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        # Alice cannot revoke bob's token.
        p.revoke_api_token("u-alice", "tok-bob")
        methods = [m for m, _, _ in log]
        self.assertNotIn("DELETE", methods)

    def test_revoke_unknown_token_is_noop(self) -> None:
        client, log = _mock_http({
            ("GET", "/Auth/Keys"): (200, {"Items": []}, ""),
        })
        p = JellyfinApiProvider(
            base_url="http://jellyfin:8096", api_key="k1", http_client=client,
        )
        p.revoke_api_token("u-alice", "tok-nonexistent")
        methods = [m for m, _, _ in log]
        self.assertNotIn("DELETE", methods)

    def test_revoke_empty_inputs_noop(self) -> None:
        p, log = self._provider((200, {"Items": []}, ""))
        p.revoke_api_token("", "tok-1")
        p.revoke_api_token("u-alice", "")
        self.assertEqual(log, [])


if __name__ == "__main__":
    unittest.main()
