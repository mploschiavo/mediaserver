"""Binding-layer tests for SessionStore.

Split from ``test_session_store.py`` to keep each test file under the
400-line comprehension cap. These tests focus on the session-token
binding surface added for the session-visibility feature:

- ``ip_prefix_for`` helper (pure function reused by LoginHistoryIndex).
- ``verify_binding`` (IP-prefix + device-class enforcement).
- The binding-populating extensions to ``create``.
- Thread safety of the full create/list/revoke mix under concurrency.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.session_store import (  # noqa: E402
    BindingStatus,
    SessionStore,
    ip_prefix_for,
)


_UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36")
_UA_PHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.0 Mobile/15E148 Safari/604.1")


class IpPrefixHelperTests(unittest.TestCase):
    def test_ipv4_prefix_is_slash_24(self):
        self.assertEqual(ip_prefix_for("203.0.113.45"), "203.0.113.0/24")
        self.assertEqual(ip_prefix_for("203.0.113.200"), "203.0.113.0/24")

    def test_ipv6_prefix_is_slash_48(self):
        self.assertEqual(ip_prefix_for("2001:db8:1:2::1"), "2001:db8:1::/48")

    def test_malformed_returns_empty(self):
        self.assertEqual(ip_prefix_for("not-an-ip"), "")
        self.assertEqual(ip_prefix_for(""), "")
        self.assertEqual(ip_prefix_for("   "), "")
        self.assertEqual(ip_prefix_for("999.999.999.999"), "")
        self.assertEqual(ip_prefix_for(None), "")  # type: ignore[arg-type]

    def test_custom_bits(self):
        self.assertEqual(
            ip_prefix_for("203.0.113.45", v4_bits=16),
            "203.0.0.0/16",
        )

    def test_whitespace_tolerated(self):
        self.assertEqual(ip_prefix_for("  203.0.113.45  "), "203.0.113.0/24")


class VerifyBindingTests(unittest.TestCase):
    def test_ok_when_observed_matches_created(self):
        s = SessionStore()
        _, plain = s.create(owner_username="alice",
                            client_ip="203.0.113.7",
                            user_agent=_UA_DESKTOP)
        status = s.verify_binding(plain,
                                   observed_ip="203.0.113.99",
                                   observed_user_agent=_UA_DESKTOP)
        self.assertEqual(status, BindingStatus.OK)

    def test_ip_mismatch_when_prefix_changes(self):
        s = SessionStore()
        _, plain = s.create(owner_username="alice",
                            client_ip="203.0.113.7",
                            user_agent=_UA_DESKTOP)
        status = s.verify_binding(plain,
                                   observed_ip="198.51.100.5",
                                   observed_user_agent=_UA_DESKTOP)
        self.assertEqual(status, BindingStatus.IP_MISMATCH)

    def test_device_mismatch_when_ua_class_changes(self):
        s = SessionStore()
        _, plain = s.create(owner_username="alice",
                            client_ip="203.0.113.7",
                            user_agent=_UA_DESKTOP)
        status = s.verify_binding(plain,
                                   observed_ip="203.0.113.7",
                                   observed_user_agent=_UA_PHONE)
        self.assertEqual(status, BindingStatus.DEVICE_MISMATCH)

    def test_unknown_session_for_random_token(self):
        s = SessionStore()
        status = s.verify_binding("never-minted-token",
                                   observed_ip="203.0.113.7",
                                   observed_user_agent=_UA_DESKTOP)
        self.assertEqual(status, BindingStatus.UNKNOWN_SESSION)

    def test_unknown_session_for_empty_token(self):
        s = SessionStore()
        self.assertEqual(
            s.verify_binding("",
                              observed_ip="203.0.113.7",
                              observed_user_agent=_UA_DESKTOP),
            BindingStatus.UNKNOWN_SESSION,
        )

    def test_unknown_session_for_expired(self):
        s = SessionStore(default_ttl_seconds=60)
        _, plain = s.create(owner_username="alice",
                            client_ip="203.0.113.7",
                            user_agent=_UA_DESKTOP,
                            now=100.0)
        # Force the session to expire by popping it through get().
        self.assertIsNone(s.get(plain, now=1000.0))
        self.assertEqual(
            s.verify_binding(plain,
                              observed_ip="203.0.113.7",
                              observed_user_agent=_UA_DESKTOP),
            BindingStatus.UNKNOWN_SESSION,
        )

    def test_legacy_session_without_binding_is_ok(self):
        """A session created by an old caller (no client_ip/user_agent)
        has empty binding fields; verify_binding treats that as "no
        binding enforced" so cookies still work."""
        s = SessionStore()
        _, plain = s.create(owner_username="alice")
        status = s.verify_binding(plain,
                                   observed_ip="203.0.113.7",
                                   observed_user_agent=_UA_DESKTOP)
        self.assertEqual(status, BindingStatus.OK)

    def test_ip_mismatch_wins_over_device_mismatch(self):
        """When both change, IP is the more alarming signal."""
        s = SessionStore()
        _, plain = s.create(owner_username="alice",
                            client_ip="203.0.113.7",
                            user_agent=_UA_DESKTOP)
        status = s.verify_binding(plain,
                                   observed_ip="198.51.100.5",
                                   observed_user_agent=_UA_PHONE)
        self.assertEqual(status, BindingStatus.IP_MISMATCH)


class CreateWithBindingTests(unittest.TestCase):
    def test_create_populates_ip_prefix_and_device_class(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice",
                            client_ip="203.0.113.45",
                            user_agent=_UA_DESKTOP)
        self.assertEqual(sess.ip_prefix, "203.0.113.0/24")
        self.assertEqual(sess.device_class, "DESKTOP")
        self.assertEqual(sess.user_agent, _UA_DESKTOP)

    def test_create_without_binding_leaves_fields_empty(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice")
        self.assertEqual(sess.ip_prefix, "")
        self.assertEqual(sess.device_class, "")
        self.assertEqual(sess.user_agent, "")
        self.assertEqual(sess.logout_reason, "")

    def test_create_with_malformed_ip_leaves_prefix_empty(self):
        s = SessionStore()
        sess, _ = s.create(owner_username="alice",
                            client_ip="not-an-ip",
                            user_agent=_UA_DESKTOP)
        self.assertEqual(sess.ip_prefix, "")
        self.assertEqual(sess.device_class, "DESKTOP")

    def test_legacy_create_signature_still_works(self):
        """Back-compat: callers that don't pass the new kwargs succeed
        and produce a usable session."""
        s = SessionStore()
        sess, plain = s.create(owner_username="alice")
        self.assertTrue(plain)
        self.assertEqual(sess.owner_username, "alice")
        self.assertIsNotNone(s.get(plain))


class ThreadSafetyTests(unittest.TestCase):
    def test_concurrent_create_list_revoke_from_ten_threads(self):
        s = SessionStore()
        errors: list[BaseException] = []
        barrier = threading.Barrier(10)

        def worker(idx: int) -> None:
            try:
                barrier.wait(timeout=5)
                made_ids: list[str] = []
                for i in range(20):
                    sess, _ = s.create(
                        owner_username=f"user{idx}",
                        client_ip=f"203.0.113.{(i % 250) + 1}",
                        user_agent=_UA_DESKTOP,
                    )
                    made_ids.append(sess.id)
                    _ = s.list_all_active()
                    _ = s.list_for(f"user{idx}")
                for sid in made_ids[::2]:
                    s.revoke_by_id(sid, reason=f"stress-{idx}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            self.assertFalse(t.is_alive(), "worker thread hung")

        self.assertEqual(errors, [])
        live = s.list_all_active()
        self.assertEqual(s.count(), len(live))
        # Each of the 10 users made 20 sessions; half (10) revoked each
        # -> 10 live per user = 100 total.
        self.assertEqual(len(live), 100)


if __name__ == "__main__":
    unittest.main()
