"""Unit tests for SessionStore (in-memory cookie session table)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_and_get(self):
        s = SessionStore()
        sess, plaintext = s.create(owner_username="alice")
        self.assertEqual(sess.owner_username, "alice")
        got = s.get(plaintext)
        self.assertIsNotNone(got)
        self.assertEqual(got.owner_username, "alice")

    def test_unknown_token_returns_none(self):
        s = SessionStore()
        self.assertIsNone(s.get("never-minted"))

    def test_expired_session_rejected(self):
        s = SessionStore(default_ttl_seconds=60)
        _, plain = s.create(owner_username="bob", now=100.0)
        # 59s in, still live
        self.assertIsNotNone(s.get(plain, now=159.0))
        # 61s in, expired
        self.assertIsNone(s.get(plain, now=161.0))

    def test_revoke_drops_session(self):
        s = SessionStore()
        _, plain = s.create(owner_username="carol")
        self.assertTrue(s.revoke(plain))
        self.assertIsNone(s.get(plain))
        # second revoke is no-op
        self.assertFalse(s.revoke(plain))

    def test_revoke_all_for_user(self):
        s = SessionStore()
        _, p1 = s.create(owner_username="dave")
        _, p2 = s.create(owner_username="dave")
        _, p3 = s.create(owner_username="eve")
        killed = s.revoke_all_for("dave")
        self.assertEqual(killed, 2)
        self.assertIsNone(s.get(p1))
        self.assertIsNone(s.get(p2))
        self.assertIsNotNone(s.get(p3))

    def test_empty_plaintext_returns_none(self):
        s = SessionStore()
        self.assertIsNone(s.get(""))
        self.assertFalse(s.revoke(""))

    def test_tokens_are_unique_per_create(self):
        s = SessionStore()
        seen = set()
        for _ in range(50):
            _, plain = s.create(owner_username="frank")
            self.assertNotIn(plain, seen)
            seen.add(plain)

    def test_hash_is_deterministic_and_different_from_plaintext(self):
        s = SessionStore()
        h1 = s.hash_token("abc")
        h2 = s.hash_token("abc")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, "abc")
        self.assertEqual(len(h1), 64)  # sha256 hex


if __name__ == "__main__":
    unittest.main()
