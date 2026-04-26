"""Unit tests for SessionStore (in-memory cookie session table).

Binding/thread-safety tests live in ``test_session_store_binding.py`` to
keep each file under the 400-line cap. This file covers the core table
mechanics: create/get/revoke, expiry/idle, and the richer list/revoke
surfaces the admin "sessions/active" UI depends on.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.session_store import SessionStore  # noqa: E402


_ISO_Z_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)


class SessionStoreCoreTests(unittest.TestCase):
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

    def test_idle_timeout_kills_inactive_session(self):
        s = SessionStore(default_ttl_seconds=8 * 60 * 60,
                         idle_ttl_seconds=60)
        _, plain = s.create(owner_username="grace", now=100.0)
        # 50s of inactivity -- still alive.
        self.assertIsNotNone(s.get(plain, now=150.0))
        # After that get(), last_used_at became 150. Another 50s later
        # we're at 200, still within the 60s window from 150 -> alive.
        self.assertIsNotNone(s.get(plain, now=200.0))
        # Now jump 120s past the last use -- session exceeds idle TTL.
        self.assertIsNone(s.get(plain, now=321.0))

    def test_active_session_slides_idle_window(self):
        s = SessionStore(idle_ttl_seconds=60)
        _, plain = s.create(owner_username="helen", now=100.0)
        # Keep using it inside the 60s window, forever.
        for t in range(130, 1000, 50):
            self.assertIsNotNone(s.get(plain, now=float(t)))

    def test_idle_disabled_when_zero(self):
        """When idle_ttl_seconds=0, inactivity never kills the
        session -- only the absolute TTL does."""
        s = SessionStore(default_ttl_seconds=3600, idle_ttl_seconds=0)
        _, plain = s.create(owner_username="ivy", now=100.0)
        # 3500s of inactivity, still within absolute TTL.
        self.assertIsNotNone(s.get(plain, now=3600.0))
        # Past absolute TTL, dies regardless of idle config.
        self.assertIsNone(s.get(plain, now=3800.0))

    def test_create_missing_username_raises(self):
        s = SessionStore()
        with self.assertRaises(ValueError):
            s.create(owner_username="")

    def test_created_at_is_iso_zulu(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="kate")
        self.assertTrue(_ISO_Z_RE.match(sess.created_at),
                        f"created_at not ISO-Z: {sess.created_at!r}")

    def test_absolute_cap_evicts_oldest(self):
        """When the cap is hit, the record with the lexically smallest
        ``created_at`` is dropped. Used to bound memory under
        pathological load."""
        import time as _time

        from media_stack.core.auth.session_store import Session
        s = SessionStore(absolute_cap=100)  # clamped to minimum 100
        far = _time.time() + 10 * 60 * 60  # 10h in the future
        # Fill to cap with sessions that have "very old" ISO timestamps
        # so the oldest-by-ISO isn't the one create() is about to add.
        for i in range(100):
            fake = Session(
                id=f"fake-{i}",
                token_hash=f"h{i:064x}",
                owner_username="filler",
                created_at=f"1900-01-01T00:00:{i:02d}.000000Z",
                expires_at=far,
                last_used_at=far,
            )
            s._sessions[fake.token_hash] = fake
        self.assertEqual(s.count(), 100)
        # Next create triggers eviction of the lexically-smallest
        # created_at ("1900-01-01T00:00:00..." -> token_hash h0000...).
        sess, _ = s.create(owner_username="new-user")
        self.assertEqual(s.count(), 100)
        self.assertNotIn(f"h{0:064x}", s._sessions)
        self.assertIn(sess.token_hash, s._sessions)


class SessionStoreRichReadsTests(unittest.TestCase):
    def test_list_for_returns_only_users_sessions(self):
        s = SessionStore()
        _, _ = s.create(owner_username="alice")
        _, _ = s.create(owner_username="alice")
        _, _ = s.create(owner_username="bob")
        alice_sessions = s.list_for("alice")
        self.assertEqual(len(alice_sessions), 2)
        self.assertTrue(all(x.owner_username == "alice" for x in alice_sessions))
        bob_sessions = s.list_for("bob")
        self.assertEqual(len(bob_sessions), 1)

    def test_list_for_ignores_expired(self):
        # Fresh session created at real wall-clock now (default 8h TTL
        # so it stays alive through the test) plus one ancient session
        # seeded far in the past (expiry clamped to year ~1970). The
        # ancient one must not appear in list_for.
        s = SessionStore()
        _, _ = s.create(owner_username="alice")           # live
        _, _ = s.create(owner_username="alice", now=100.0)  # ancient
        alice = s.list_for("alice")
        self.assertEqual(len(alice), 1)

    def test_list_for_unknown_user(self):
        s = SessionStore()
        self.assertEqual(s.list_for("nobody"), [])
        self.assertEqual(s.list_for(""), [])

    def test_list_all_active_excludes_expired_and_orders_desc(self):
        # Leave default (8h) TTL so alice/bob survive the test run; the
        # only expired record is the one we seeded "at 100s after the
        # epoch", whose expires_at is well before any real wall clock.
        s = SessionStore(idle_ttl_seconds=0)
        _, _ = s.create(owner_username="old", now=100.0)  # ancient
        _, _ = s.create(owner_username="alice")
        _, p_b = s.create(owner_username="bob")
        # Bump bob's last_used_at so he's first.
        s.get(p_b)
        active = s.list_all_active()
        self.assertEqual(len(active), 2, [x.owner_username for x in active])
        self.assertEqual(active[0].owner_username, "bob")
        self.assertEqual(active[1].owner_username, "alice")

    def test_get_returns_none_for_unknown_session_id(self):
        s = SessionStore()
        self.assertIsNone(s.get("not-a-real-id"))

    def test_get_by_session_id(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice")
        got = s.get(sess.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.id, sess.id)
        self.assertEqual(got.owner_username, "alice")

    def test_get_by_session_id_returns_none_when_expired(self):
        # An expired session that hasn't been evicted yet must still be
        # reported as gone via the admin id-lookup path.
        s = SessionStore(default_ttl_seconds=60)
        sess, _ = s.create(owner_username="alice", now=100.0)
        # Manually rewind expires so it's in the past without going
        # through get() (which would also evict it).
        sess.expires_at = 50.0
        self.assertIsNone(s.get(sess.id))


class SessionStoreRevokeByIdTests(unittest.TestCase):
    def test_revoke_by_id_returns_true_on_success(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice")
        self.assertTrue(s.revoke_by_id(sess.id))
        self.assertIsNone(s.get(sess.id))

    def test_revoke_by_id_returns_false_on_missing(self):
        s = SessionStore()
        self.assertFalse(s.revoke_by_id("no-such-id"))
        self.assertFalse(s.revoke_by_id(""))

    def test_revoke_by_id_records_reason_on_removed_record(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice")
        # Caller holds its own reference BEFORE revoke.
        ref = sess
        self.assertTrue(s.revoke_by_id(sess.id, reason="suspicious_ip"))
        self.assertEqual(ref.logout_reason, "suspicious_ip")

    def test_revoke_by_id_default_reason(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice")
        ref = sess
        self.assertTrue(s.revoke_by_id(sess.id))
        self.assertEqual(ref.logout_reason, "admin_revoke")

    def test_revoke_by_id_empty_reason_falls_back(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice")
        ref = sess
        self.assertTrue(s.revoke_by_id(sess.id, reason=""))
        self.assertEqual(ref.logout_reason, "admin_revoke")

    def test_revoke_by_id_second_call_returns_false(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice")
        self.assertTrue(s.revoke_by_id(sess.id))
        self.assertFalse(s.revoke_by_id(sess.id))


if __name__ == "__main__":
    unittest.main()
