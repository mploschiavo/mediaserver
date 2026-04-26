"""Tests for the controller-owned user store (JSON backed)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.models import UserState  # noqa: E402
from media_stack.core.auth.users.user_store import UserStore  # noqa: E402


class UserStoreTests(unittest.TestCase):
    def _store(self, tmp: str) -> UserStore:
        return UserStore(Path(tmp) / "users.json")

    def test_create_and_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            u = s.create(email="jane@x", username="jane",
                         display_name="Jane", role_slug="adult")
            self.assertEqual(u.email, "jane@x")
            self.assertEqual(u.state, UserState.ACTIVE)
            self.assertTrue(u.id)
            got = s.get(u.id)
            self.assertEqual(got.email, "jane@x")

    def test_rejects_duplicate_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            s.create(email="a@x", username="a", display_name="A", role_slug="adult")
            with self.assertRaises(ValueError):
                s.create(email="a@x", username="b", display_name="B", role_slug="adult")

    def test_rejects_duplicate_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            s.create(email="a@x", username="jane", display_name="A", role_slug="adult")
            with self.assertRaises(ValueError):
                s.create(email="b@x", username="jane", display_name="B", role_slug="adult")

    def test_update_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            u = s.create(email="a@x", username="a", display_name="A", role_slug="adult")
            s.update(u.id, role_slug="teen", provider_refs={"authelia": "a"})
            # Reload from disk
            s2 = self._store(tmp)
            got = s2.get(u.id)
            self.assertEqual(got.role_slug, "teen")
            self.assertEqual(got.provider_refs, {"authelia": "a"})

    def test_provider_refs_merge_not_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            u = s.create(email="a@x", username="a", display_name="A", role_slug="adult")
            s.update(u.id, provider_refs={"authelia": "a"})
            s.update(u.id, provider_refs={"jellyfin": "jf-1"})
            got = s.get(u.id)
            self.assertEqual(got.provider_refs, {"authelia": "a", "jellyfin": "jf-1"})

    def test_soft_delete_hides_from_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            u = s.create(email="a@x", username="a", display_name="A", role_slug="adult")
            s.soft_delete(u.id)
            self.assertEqual(s.list_all(), [])
            self.assertEqual(len(s.list_all(include_deleted=True)), 1)

    def test_get_by_email_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            s.create(email="Jane@X.com", username="jane", display_name="", role_slug="adult")
            self.assertIsNotNone(s.get_by_email("jane@x.com"))

    def test_email_reuse_allowed_after_soft_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp)
            u1 = s.create(email="a@x", username="a", display_name="", role_slug="adult")
            s.soft_delete(u1.id)
            # Reusing email is allowed because the old record is deleted
            u2 = s.create(email="a@x", username="a2", display_name="", role_slug="adult")
            self.assertNotEqual(u1.id, u2.id)


if __name__ == "__main__":
    unittest.main()
