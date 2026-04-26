"""Unit tests for SecurityCounters."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.observability.security_counters import (
    SecurityCounters,
)


class SecurityCountersTests(unittest.TestCase):
    def test_default_events_start_at_zero(self):
        c = SecurityCounters()
        snap = c.snapshot()
        for name in ("sudo_fail", "hmac_fail", "ip_lockout_trip",
                     "csrf_fail", "auth_fail"):
            self.assertIn(name, snap)
            self.assertEqual(snap[name], 0)

    def test_incr_bumps_counter(self):
        c = SecurityCounters()
        c.incr("sudo_fail")
        c.incr("sudo_fail")
        c.incr("sudo_fail", 3)
        self.assertEqual(c.snapshot()["sudo_fail"], 5)

    def test_incr_with_zero_or_negative_is_noop(self):
        c = SecurityCounters()
        c.incr("sudo_fail", 0)
        c.incr("sudo_fail", -5)
        self.assertEqual(c.snapshot()["sudo_fail"], 0)

    def test_unknown_event_is_accepted(self):
        """Callers can add new event names without editing the
        counter module — useful when security layers evolve."""
        c = SecurityCounters()
        c.incr("new_event_xyz")
        self.assertEqual(c.snapshot()["new_event_xyz"], 1)

    def test_reset_clears_all(self):
        c = SecurityCounters()
        c.incr("sudo_fail", 7)
        c.reset()
        self.assertEqual(c.snapshot()["sudo_fail"], 0)

    def test_thread_safety(self):
        """Many threads hammering the same event must not lose writes."""
        c = SecurityCounters()
        workers = 20
        per_worker = 500

        def hammer():
            for _ in range(per_worker):
                c.incr("csrf_fail")

        threads = [threading.Thread(target=hammer) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(c.snapshot()["csrf_fail"], workers * per_worker)


if __name__ == "__main__":
    unittest.main()
