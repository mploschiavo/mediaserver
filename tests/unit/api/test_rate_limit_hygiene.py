"""Rate limiter / IP lockout hygiene tests.

The 2026-04-19 incident: after a round of testing + a few login
retries, `http://localhost:9100/` started returning 429. The dev's
own box got IP-locked because every failed auth attempt counted
against 127.0.0.1, and the smoke test suite never re-hit after a
deliberate lockout trip.

These tests lock in two properties the rate limiter MUST satisfy
to be usable in production:

  1. Loopback (127.0.0.1, ::1) is never locked out — a dev box or
     a same-machine reverse proxy looks like loopback, and
     locking that client pushes everyone off the dashboard.
  2. After the cooldown window, a previously-locked IP can try
     again. Forgotten cooldown logic = permanent lockout.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.failed_login_tracker import (  # noqa: E402
    FailedLoginTracker,
)


class IpLockoutBehaviourTests(unittest.TestCase):
    """Mechanics of the per-IP failed-login tracker itself."""

    def _tracker(self) -> FailedLoginTracker:
        # Tight values so the test runs fast. Threshold=3 → 3
        # failures triggers lock; window=10s, cooldown=5s.
        return FailedLoginTracker(threshold=3, window_seconds=10,
                                   cooldown_seconds=5)

    def test_below_threshold_not_locked(self):
        """Two failures must not trigger — threshold is 3."""
        t = self._tracker()
        t.register_failure("10.0.0.1", now=1.0)
        t.register_failure("10.0.0.1", now=2.0)
        self.assertFalse(t.is_locked("10.0.0.1", now=3.0))

    def test_at_threshold_is_locked(self):
        t = self._tracker()
        for i in range(3):
            t.register_failure("10.0.0.1", now=1.0 + i)
        self.assertTrue(t.is_locked("10.0.0.1", now=5.0))

    def test_lock_clears_after_cooldown(self):
        """Without this, a one-time credential-stuffing burst
        permanently locks out a home-IP admin."""
        t = self._tracker()
        for i in range(3):
            t.register_failure("10.0.0.1", now=1.0 + i)
        self.assertTrue(t.is_locked("10.0.0.1", now=5.0))
        # 5s cooldown, threshold locked at t=3.0 → unlocks after t=8.0
        self.assertFalse(t.is_locked("10.0.0.1", now=100.0))

    def test_old_failures_outside_window_dont_count(self):
        """Sliding window: failures older than window_seconds must
        be ignored. Otherwise a user who gets one password wrong
        a week ago is halfway to being locked out today."""
        t = self._tracker()
        t.register_failure("10.0.0.1", now=1.0)
        t.register_failure("10.0.0.1", now=2.0)
        # 10s window — t=3.0 failure + t=1.0/2.0 failures still
        # in window → locked.
        t.register_failure("10.0.0.1", now=3.0)
        self.assertTrue(t.is_locked("10.0.0.1", now=4.0))
        # But 12s later those are all outside the window.
        # Need to reset the counter by checking lock state after
        # window expiry. Different implementations handle this
        # differently; assert at least one tracker flow recovers.
        t2 = self._tracker()
        t2.register_failure("10.0.0.1", now=1.0)
        t2.register_failure("10.0.0.1", now=2.0)
        # 11s later, beyond the 10s window, failures expire.
        t2.register_failure("10.0.0.1", now=13.0)
        self.assertFalse(
            t2.is_locked("10.0.0.1", now=14.0),
            "Failures older than window_seconds are still counting. "
            "A user who typoed their password last week would be "
            "halfway to a lockout today.",
        )

    def test_different_ips_tracked_independently(self):
        """One attacker's IP getting locked must NOT lock out a
        separate legitimate admin at a different IP."""
        t = self._tracker()
        for i in range(3):
            t.register_failure("10.0.0.1", now=1.0 + i)
        self.assertTrue(t.is_locked("10.0.0.1", now=5.0))
        self.assertFalse(t.is_locked("10.0.0.2", now=5.0),
                         "Cross-IP contamination in tracker — "
                         "one attacker locks out everyone.")


class LoopbackExemptionPolicyTests(unittest.TestCase):
    """Server-side: whatever the tracker says, loopback MUST never
    see a 429 from the lockout path. This is the test that would
    have caught the 'localhost got locked out after my own test
    runs' incident."""

    def test_loopback_v4_is_exempt_from_lockout_decision(self):
        """Even when the tracker says an IP is locked, a loopback
        IP must pass the gate. Directly tests the policy function."""
        from media_stack.api.server import (
            _should_reject_for_ip_lockout, _ip_failure_tracker,
        )
        # Force the tracker to mark 127.0.0.1 as locked.
        for _ in range(25):
            _ip_failure_tracker.register_failure("127.0.0.1")
        self.assertFalse(
            _should_reject_for_ip_lockout("127.0.0.1"),
            "Policy sent a locked loopback IP to 429 — dev box "
            "and same-host reverse proxy clients would be "
            "locked out of their own dashboard.",
        )

    def test_loopback_v6_is_exempt_from_lockout_decision(self):
        from media_stack.api.server import (
            _should_reject_for_ip_lockout, _ip_failure_tracker,
        )
        for _ in range(25):
            _ip_failure_tracker.register_failure("::1")
        self.assertFalse(
            _should_reject_for_ip_lockout("::1"),
            "IPv6 loopback not exempt — modern macOS default "
            "to ::1 and would hit the lockout instead of auth.",
        )

    def test_external_ip_still_respects_lockout(self):
        """Exempting loopback must not make us turn off the lockout
        entirely. A real external IP, once tripped, must still 429."""
        from media_stack.api.server import (
            _should_reject_for_ip_lockout, _ip_failure_tracker,
        )
        for _ in range(25):
            _ip_failure_tracker.register_failure("1.2.3.4")
        self.assertTrue(
            _should_reject_for_ip_lockout("1.2.3.4"),
            "IP lockout not enforced for external IPs — the "
            "whole brute-force defense is off.",
        )

    def test_docker_bridge_gateway_is_exempt(self):
        """The 'I hit localhost:9100 in the browser and got 429'
        failure mode. Browser→docker-proxy→container translates
        the source IP to the compose-network gateway (172.x.x.1
        typically), NOT 127.0.0.1. A literal-loopback-only
        exemption misses it. Private ranges must all be exempt."""
        from media_stack.api.server import (
            _should_reject_for_ip_lockout, _ip_failure_tracker,
        )
        for bridge_ip in ("172.21.0.1", "172.17.0.1", "10.0.0.5",
                           "192.168.1.100"):
            # Bury this IP under many failures.
            for _ in range(30):
                _ip_failure_tracker.register_failure(bridge_ip)
            self.assertFalse(
                _should_reject_for_ip_lockout(bridge_ip),
                f"Private-range IP {bridge_ip!r} got rejected by "
                "lockout. The browser-via-docker-proxy path would "
                "see the dashboard as 429 after a few test runs.",
            )

    def test_public_ip_still_gets_locked(self):
        """Sanity: after exempting private ranges, PUBLIC IPs must
        still feel the lockout. Otherwise the brute-force
        protection is a no-op."""
        from media_stack.api.server import (
            _should_reject_for_ip_lockout, _ip_failure_tracker,
        )
        for _ in range(30):
            _ip_failure_tracker.register_failure("8.8.8.8")
        self.assertTrue(
            _should_reject_for_ip_lockout("8.8.8.8"),
            "Public IP not locked out — brute-force defense off.",
        )


if __name__ == "__main__":
    unittest.main()
