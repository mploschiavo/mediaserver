"""Unit tests for the per-IP failed-login lockout.

Verifies that:
  - is_locked() returns False until the threshold is crossed
  - is_locked() stays True for the whole cooldown window
  - Eventual time travel past the cooldown re-opens the IP

We use FailedLoginTracker directly (not the server.py wrapper) because
lockout semantics live in the tracker; server.py just wires it to
handler.client_address[0].
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.failed_login_tracker import FailedLoginTracker


class IpLockoutTests(unittest.TestCase):
    def _tracker(self, **kw):
        return FailedLoginTracker(threshold=5, window_seconds=60,
                                  cooldown_seconds=300, **kw)

    def test_not_locked_before_threshold(self):
        t = self._tracker()
        for i in range(4):
            t.register_failure("203.0.113.7", now=100.0 + i)
        self.assertFalse(t.is_locked("203.0.113.7", now=104.0))

    def test_locks_after_threshold_crossing(self):
        t = self._tracker()
        for i in range(5):
            t.register_failure("203.0.113.7", now=100.0 + i)
        self.assertTrue(t.is_locked("203.0.113.7", now=110.0))

    def test_lock_holds_for_cooldown(self):
        t = self._tracker()
        for i in range(5):
            t.register_failure("203.0.113.7", now=100.0 + i)
        # 299s into the 300s cooldown — still locked
        self.assertTrue(t.is_locked("203.0.113.7", now=399.0))

    def test_lock_expires_after_cooldown(self):
        t = self._tracker()
        for i in range(5):
            t.register_failure("203.0.113.7", now=100.0 + i)
        # One second past cooldown — unlocked
        self.assertFalse(t.is_locked("203.0.113.7", now=501.0))

    def test_other_ips_unaffected(self):
        t = self._tracker()
        for i in range(5):
            t.register_failure("203.0.113.7", now=100.0 + i)
        self.assertTrue(t.is_locked("203.0.113.7", now=110.0))
        self.assertFalse(t.is_locked("198.51.100.1", now=110.0))

    def test_success_does_not_clear_lock(self):
        """register_success clears the username's window only. Lock on
        one IP is independent of another user's successful login."""
        t = self._tracker()
        for i in range(5):
            t.register_failure("203.0.113.7", now=100.0 + i)
        # Success is keyed by username; lock is keyed by IP. The
        # tracker is generic — register_success('alice') doesn't
        # clear the '203.0.113.7' bucket.
        t.register_success("alice")
        self.assertTrue(t.is_locked("203.0.113.7", now=110.0))


if __name__ == "__main__":
    unittest.main()
