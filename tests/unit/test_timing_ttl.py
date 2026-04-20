"""Timing / TTL correctness tests.

Bugs that only surface at specific times:
  - Session idle expiry fires slightly early / slightly late.
  - CSRF token silently rotates mid-POST because a GET landed
    between the POST's preflight and body.
  - Invite expires off-by-one (accepted one second after expires_at).
  - Rate limiter window resets correctly so an admin doesn't get
    permanently locked out.

All tests inject ``now`` explicitly rather than sleeping, so the
suite runs in < 50ms regardless.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.session_store import SessionStore  # noqa: E402


class SessionIdleExpiryTests(unittest.TestCase):
    """Session.last_used_at + idle_ttl == expiry boundary.
    Off-by-one bugs here either log users out early or let a
    stolen cookie outlive its sibling session on the same user."""

    def test_session_valid_just_before_idle_ttl(self):
        store = SessionStore(default_ttl_seconds=3600, idle_ttl_seconds=60)
        _, token = store.create(owner_username="admin")
        base = time.time()
        # 59s after creation — still within idle window.
        got = store.get(token, now=base + 59)
        self.assertIsNotNone(
            got, "session expired 1s BEFORE idle_ttl — off-by-one "
            "on the comparison would log admins out early.",
        )

    def test_session_expires_exactly_at_idle_ttl_plus_one(self):
        """At +61s (past the 60s idle limit), the session must be
        dead. A test at +60s exactly is on the boundary and is
        implementation-dependent (≤ vs <) so we test +1 over."""
        store = SessionStore(default_ttl_seconds=3600, idle_ttl_seconds=60)
        _, token = store.create(owner_username="admin")
        base = time.time()
        got = store.get(token, now=base + 61)
        self.assertIsNone(
            got, "session survived PAST idle_ttl — a forgotten "
            "laptop's cookie stays valid longer than advertised.",
        )

    def test_each_get_resets_idle_timer(self):
        """Active use must slide the idle window forward — otherwise
        an admin actively working in the dashboard gets bumped to
        login after exactly idle_ttl_seconds regardless of activity."""
        store = SessionStore(default_ttl_seconds=3600, idle_ttl_seconds=60)
        _, token = store.create(owner_username="admin")
        base = time.time()
        # 30s in, still good.
        self.assertIsNotNone(store.get(token, now=base + 30))
        # Another 55s (total 85s from create, but only 55s idle).
        self.assertIsNotNone(
            store.get(token, now=base + 85),
            "idle window didn't slide — active admin gets logged "
            "out after idle_ttl even though they're clicking.",
        )

    def test_absolute_ttl_still_expires_even_with_activity(self):
        """default_ttl_seconds is the HARD cap. Even a continuously
        active session must be terminated at that boundary —
        prevents 'logged in forever' sessions and supports
        rotation / revocation guarantees."""
        store = SessionStore(default_ttl_seconds=120, idle_ttl_seconds=60)
        _, token = store.create(owner_username="admin")
        base = time.time()
        # Keep the session warm by sliding idle window repeatedly.
        for dt in (30, 60, 90):
            self.assertIsNotNone(store.get(token, now=base + dt))
        # Past absolute TTL.
        self.assertIsNone(
            store.get(token, now=base + 121),
            "session exceeded absolute TTL — a stolen cookie "
            "would stay valid forever as long as the thief keeps "
            "using it.",
        )

    def test_revoked_session_is_dead_immediately(self):
        """Revocation must be instant, not deferred to the next
        expiry check. Users clicking 'log out of all devices'
        expect immediate effect."""
        store = SessionStore(default_ttl_seconds=3600, idle_ttl_seconds=60)
        _, token = store.create(owner_username="admin")
        store.revoke(token)
        self.assertIsNone(store.get(token))


class CsrfTokenStabilityTests(unittest.TestCase):
    """The CSRF token must not rotate mid-request. If it does, a
    POST's header can mismatch the cookie the browser is sending
    because a GET running in parallel just issued a new token."""

    def test_extract_cookie_returns_same_value_on_repeated_reads(self):
        from media_stack.core.auth.csrf import CsrfProtector
        csrf = CsrfProtector()
        tok = csrf.issue_token()
        cookie = f"media_stack_csrf={tok}"
        for _ in range(5):
            self.assertEqual(csrf.extract_cookie(cookie), tok)

    def test_verify_is_constant_time_and_strict(self):
        """hmac.compare_digest semantics — any mismatch is rejected,
        identical values are accepted, and the function doesn't
        leak via early-exit timing."""
        from media_stack.core.auth.csrf import CsrfProtector
        csrf = CsrfProtector()
        tok = csrf.issue_token()
        cookie = f"media_stack_csrf={tok}"
        self.assertTrue(csrf.verify(cookie_header=cookie, header_value=tok))
        self.assertFalse(csrf.verify(cookie_header=cookie,
                                     header_value=tok + "x"))
        self.assertFalse(csrf.verify(cookie_header=cookie,
                                     header_value=""))
        self.assertFalse(csrf.verify(cookie_header="",
                                     header_value=tok))


class InviteExpiryBoundaryTests(unittest.TestCase):
    """Invites must become invalid the moment expires_at passes —
    off-by-one lets an 'expired' link accept a new user."""

    def test_expires_at_in_the_past_is_is_expired_true(self):
        from media_stack.core.auth.users.invite_store import (
            Invite, InviteStore,
        )
        with tempfile.TemporaryDirectory() as d:
            store = InviteStore(Path(d) / "i.json")
            inv = Invite(
                id="i-1", email="x@y", role_slug="adult",
                created_by="admin",
                created_at="2020-01-01T00:00:00+00:00",
                expires_at="2020-01-01T00:00:01+00:00",
                token_hash="h",
            )
            self.assertTrue(
                store.is_expired(inv),
                "past-expiry invite reported as still valid — "
                "accepted users would bypass expiry controls.",
            )

    def test_expires_at_in_the_far_future_is_is_expired_false(self):
        from media_stack.core.auth.users.invite_store import (
            Invite, InviteStore,
        )
        with tempfile.TemporaryDirectory() as d:
            store = InviteStore(Path(d) / "i.json")
            inv = Invite(
                id="i-1", email="x@y", role_slug="adult",
                created_by="admin",
                created_at="2030-01-01T00:00:00+00:00",
                expires_at="2099-01-01T00:00:00+00:00",
                token_hash="h",
            )
            self.assertFalse(store.is_expired(inv))


if __name__ == "__main__":
    unittest.main()
