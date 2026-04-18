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


if __name__ == "__main__":
    unittest.main()
