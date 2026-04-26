"""Tests for AutheliaFileProvider."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.authelia.user_provider import (  # noqa: E402
    AutheliaFileProvider, AutheliaProviderError,
)


class AutheliaFileProviderTests(unittest.TestCase):
    def _provider(self, tmp: str) -> AutheliaFileProvider:
        path = Path(tmp) / "users_database.yml"
        return AutheliaFileProvider(users_db_path=path)

    def test_create_writes_user_with_hashed_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            ext = p.create_user(
                username="jane", email="jane@x", display_name="Jane",
                password="supersecret", groups=["family", "admins"],
            )
            self.assertEqual(ext.external_id, "jane")
            data = yaml.safe_load((Path(tmp) / "users_database.yml").read_text())
            entry = data["users"]["jane"]
            # Hash, not plaintext
            self.assertTrue(entry["password"].startswith("$argon2id$"))
            self.assertNotIn("supersecret", entry["password"])
            self.assertEqual(entry["email"], "jane@x")
            self.assertEqual(entry["groups"], ["family", "admins"])

    def test_create_existing_user_raises(self):
        from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditError
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            p.create_user(username="a", email="a@x", display_name="A",
                          password="pw", groups=[])
            # Mutator raises AutheliaProviderError; editor wraps as SafeYamlEditError.
            # Both are acceptable — callers just need a failure signal with context.
            with self.assertRaises((AutheliaProviderError, SafeYamlEditError)) as ctx:
                p.create_user(username="a", email="a2@x", display_name="A2",
                              password="pw", groups=[])
            self.assertIn("already exists", str(ctx.exception))

    def test_list_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            p.create_user(username="a", email="a@x", display_name="A", password="pw", groups=["admins"])
            p.create_user(username="b", email="b@x", display_name="B", password="pw", groups=[])
            users = p.list_users()
            self.assertEqual({u.username for u in users}, {"a", "b"})

    def test_update_changes_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            p.create_user(username="a", email="a@x", display_name="A", password="pw", groups=["adults"])
            p.update_user("a", groups=["admins", "family"])
            entry = yaml.safe_load((Path(tmp) / "users_database.yml").read_text())["users"]["a"]
            self.assertEqual(entry["groups"], ["admins", "family"])

    def test_set_password_changes_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            p.create_user(username="a", email="a@x", display_name="A", password="pw1", groups=[])
            h1 = yaml.safe_load((Path(tmp) / "users_database.yml").read_text())["users"]["a"]["password"]
            p.set_password("a", "pw2")
            h2 = yaml.safe_load((Path(tmp) / "users_database.yml").read_text())["users"]["a"]["password"]
            self.assertNotEqual(h1, h2)

    def test_delete_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            p.create_user(username="a", email="a@x", display_name="A", password="pw", groups=[])
            p.delete_user("a")
            # Second delete must not raise
            p.delete_user("a")
            data = yaml.safe_load((Path(tmp) / "users_database.yml").read_text()) or {}
            self.assertNotIn("a", (data.get("users") or {}))

    def test_update_missing_user_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            p.create_user(username="a", email="a@x", display_name="", password="pw", groups=[])
            with self.assertRaises(Exception):
                p.update_user("missing", groups=["x"])

    def test_health_check_flags_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            health = p.health_check()
            self.assertFalse(health.ok)
            self.assertIn("missing", health.detail)

    def test_validator_allows_entry_without_password(self):
        """Password absence is allowed (Authelia itself accepts this) — but
        an explicitly-empty password must still be rejected as corrupt.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users_database.yml"
            path.write_text(yaml.safe_dump({"users": {"pending": {"email": "x@x"}}}))
            p = AutheliaFileProvider(users_db_path=path)
            # Missing password key should not block an unrelated update
            p.update_user("pending", groups=["new"])

    def test_validator_rejects_empty_password_string(self):
        from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditError
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users_database.yml"
            path.write_text(yaml.safe_dump({"users": {"bad": {"email": "x@x", "password": ""}}}))
            p = AutheliaFileProvider(users_db_path=path)
            with self.assertRaises(SafeYamlEditError):
                p.update_user("bad", groups=["x"])


class AutheliaAccountStateTests(unittest.TestCase):
    """Coverage for disable/enable/is_disabled and the protocol
    conformance the session-visibility feature requires."""

    def _provider_with_user(self, tmp: str, username: str = "jane"):
        p = AutheliaFileProvider(users_db_path=Path(tmp) / "users_database.yml")
        p.create_user(
            username=username, email=f"{username}@x",
            display_name=username.capitalize(), password="pw",
            groups=["family"],
        )
        return p

    def test_user_starts_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            self.assertFalse(p.is_disabled("jane"))

    def test_disable_sets_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            p.disable_user("jane")
            self.assertTrue(p.is_disabled("jane"))
            data = yaml.safe_load((Path(tmp) / "users_database.yml").read_text())
            self.assertIs(data["users"]["jane"]["disabled"], True)

    def test_enable_clears_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            p.disable_user("jane")
            p.enable_user("jane")
            self.assertFalse(p.is_disabled("jane"))
            data = yaml.safe_load((Path(tmp) / "users_database.yml").read_text())
            # We remove the key entirely when enabling, rather than
            # setting it to false — keeps the yaml minimal so admins
            # reading the file don't see noise.
            self.assertNotIn("disabled", data["users"]["jane"])

    def test_disable_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            p.disable_user("jane")
            p.disable_user("jane")  # twice
            self.assertTrue(p.is_disabled("jane"))

    def test_enable_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            p.enable_user("jane")  # already enabled
            self.assertFalse(p.is_disabled("jane"))

    def test_disable_missing_user_raises(self):
        from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditError
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            with self.assertRaises((AutheliaProviderError, SafeYamlEditError)):
                p.disable_user("nobody")

    def test_is_disabled_missing_user_returns_false(self):
        # Defensive: caller asked about an unknown user; don't raise.
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            self.assertFalse(p.is_disabled("nobody"))

    def test_is_disabled_missing_file_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = AutheliaFileProvider(
                users_db_path=Path(tmp) / "does_not_exist.yml",
            )
            self.assertFalse(p.is_disabled("jane"))

    def test_satisfies_account_state_protocol(self):
        from media_stack.core.auth.users.visibility_protocols import (
            AccountStateProvider,
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider_with_user(tmp)
            self.assertIsInstance(p, AccountStateProvider)


class AutheliaOptionalProtocolsTests(unittest.TestCase):
    """Authelia file backend implements these as conservative no-op /
    empty — real enforcement lives in a separate session-admin impl
    that reads Authelia's sqlite DB."""

    def _provider(self, tmp: str) -> AutheliaFileProvider:
        return AutheliaFileProvider(users_db_path=Path(tmp) / "users_database.yml")

    def test_revoke_session_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            # Neither known nor unknown session raises.
            p.revoke_session("jane", "session-xyz")
            p.revoke_session("nobody", "unknown")

    def test_mfa_state_is_none(self):
        from media_stack.core.auth.users.visibility_protocols import MFAState
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            state = p.mfa_state("jane")
            self.assertIsInstance(state, MFAState)
            self.assertFalse(state.enrolled)

    def test_list_api_tokens_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            self.assertEqual(p.list_api_tokens("jane"), [])

    def test_revoke_api_token_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            p.revoke_api_token("jane", "tk-1")

    def test_satisfies_session_admin_protocol(self):
        from media_stack.core.auth.users.visibility_protocols import (
            SessionAdminProvider,
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            self.assertIsInstance(p, SessionAdminProvider)


if __name__ == "__main__":
    unittest.main()
