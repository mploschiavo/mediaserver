"""Tests for the derived login-history index.

Covers the three signals the Security tab of the admin UI depends on:

* first-seen-IP (new /24 vs. known /24, with lookback expiry).
* concurrent session count (live only, expired ignored).
* impossible-travel anomaly (different /16 inside a time window,
  IPv4 and IPv6).

Also exercises the thread-safety contract and the protocol
conformance that downstream consumers will rely on.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.session_store import SessionStore  # noqa: E402
from media_stack.core.auth.users.audit_actions import (  # noqa: E402
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
)
from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402
from media_stack.core.auth.users.login_history_index import (  # noqa: E402
    LoginEvent,
    LoginHistoryIndex,
    LoginHistoryProtocol,
)
from media_stack.core.time_utils import utcnow_iso  # noqa: E402


def _iso(dt: datetime) -> str:
    """Format a datetime the same way ``utcnow_iso`` does."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _now_iso() -> str:
    return utcnow_iso()


def _make_index(tmp: str) -> tuple[LoginHistoryIndex, AuditLog, SessionStore]:
    audit = AuditLog(Path(tmp) / "audit.log.jsonl")
    sessions = SessionStore()
    return LoginHistoryIndex(audit, sessions), audit, sessions


class LoginEventDataclassTests(unittest.TestCase):
    def test_login_event_is_frozen(self):
        evt = LoginEvent(ip_prefix_16="10.0.0.0/16",
                         ip_prefix_24="10.0.1.0/24",
                         ts_iso=_now_iso())
        with self.assertRaises(Exception):
            evt.ts_iso = "nope"  # type: ignore[misc]


class RebuildTests(unittest.TestCase):
    def test_rebuild_reflects_every_login_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, audit, _ = _make_index(tmp)
            audit.append(actor="alice", action=LOGIN_SUCCESS, target="alice",
                         ip="203.0.113.5")
            audit.append(actor="alice", action=LOGIN_SUCCESS, target="alice",
                         ip="203.0.113.99")
            audit.append(actor="bob", action=LOGIN_SUCCESS, target="bob",
                         ip="198.51.100.1")
            idx.rebuild()
            # Both alice IPs collapse to one /24; bob has his own row.
            self.assertFalse(idx.is_first_seen_ip("alice", "203.0.113.42"))
            self.assertFalse(idx.is_first_seen_ip("bob", "198.51.100.77"))
            self.assertTrue(idx.is_first_seen_ip("alice", "10.9.8.7"))

    def test_rebuild_ignores_non_auth_and_non_success_and_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, audit, _ = _make_index(tmp)
            audit.append(actor="alice", action=LOGIN_FAILURE, target="alice",
                         ip="203.0.113.5")
            audit.append(actor="alice", action="create_user", target="alice",
                         ip="203.0.113.5")
            # LOGIN_SUCCESS without a target falls back to actor.
            audit.append(actor="carol", action=LOGIN_SUCCESS, target="",
                         ip="198.51.100.9")
            # LOGIN_SUCCESS without an IP is skipped.
            audit.append(actor="dave", action=LOGIN_SUCCESS, target="dave",
                         ip="")
            # LOGIN_SUCCESS with bogus IP is skipped silently.
            audit.append(actor="eve", action=LOGIN_SUCCESS, target="eve",
                         ip="not-an-ip")
            idx.rebuild()
            self.assertTrue(idx.is_first_seen_ip("alice", "203.0.113.5"))
            self.assertFalse(idx.is_first_seen_ip("carol", "198.51.100.1"))
            self.assertTrue(idx.is_first_seen_ip("dave", "203.0.113.5"))
            self.assertTrue(idx.is_first_seen_ip("eve", "203.0.113.5"))

    def test_rebuild_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, audit, _ = _make_index(tmp)
            audit.append(actor="alice", action=LOGIN_SUCCESS, target="alice",
                         ip="203.0.113.5")
            idx.rebuild()
            idx.rebuild()
            self.assertFalse(idx.is_first_seen_ip("alice", "203.0.113.9"))


class ObserveTests(unittest.TestCase):
    def test_observe_updates_first_seen_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            self.assertTrue(idx.is_first_seen_ip("alice", "203.0.113.5"))
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_now_iso())
            self.assertFalse(idx.is_first_seen_ip("alice", "203.0.113.5"))
            # A different /24 is still first-seen.
            self.assertTrue(idx.is_first_seen_ip("alice", "198.51.100.1"))

    def test_observe_ignores_empty_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            idx.observe(username="", client_ip="203.0.113.5",
                        ts_iso=_now_iso())
            idx.observe(username="alice", client_ip="",
                        ts_iso=_now_iso())
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso="")
            self.assertTrue(idx.is_first_seen_ip("alice", "203.0.113.5"))

    def test_observe_ignores_malformed_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            idx.observe(username="alice", client_ip="garbage",
                        ts_iso=_now_iso())
            # Still first-seen because malformed IPs are discarded.
            self.assertTrue(idx.is_first_seen_ip("alice", "203.0.113.5"))

    def test_observe_keeps_newer_timestamp_on_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            old = _iso(datetime.now(timezone.utc) - timedelta(days=200))
            new = _now_iso()
            idx.observe(username="alice", client_ip="203.0.113.5", ts_iso=old)
            idx.observe(username="alice", client_ip="203.0.113.5", ts_iso=new)
            # With the newer timestamp, the prefix is known.
            self.assertFalse(idx.is_first_seen_ip("alice", "203.0.113.9"))
            # Even a second older write doesn't clobber the newer value.
            older = _iso(datetime.now(timezone.utc) - timedelta(days=400))
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=older)
            self.assertFalse(idx.is_first_seen_ip("alice", "203.0.113.9"))


class FirstSeenLookbackTests(unittest.TestCase):
    def test_new_prefix_is_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            self.assertTrue(idx.is_first_seen_ip("alice", "203.0.113.5"))

    def test_known_prefix_is_not_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_now_iso())
            self.assertFalse(idx.is_first_seen_ip("alice", "203.0.113.200"))

    def test_known_prefix_expires_past_lookback(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            stale = _iso(datetime.now(timezone.utc) - timedelta(days=200))
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=stale)
            # Default 90-day lookback: stale record has aged out.
            self.assertTrue(idx.is_first_seen_ip("alice", "203.0.113.9"))
            # Widen the window and the same record is in range.
            self.assertFalse(idx.is_first_seen_ip(
                "alice", "203.0.113.9", lookback_days=365))

    def test_first_seen_rejects_empty_and_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            # Empty username / empty ip / malformed ip → False (we
            # don't flag what we can't reason about).
            self.assertFalse(idx.is_first_seen_ip("", "203.0.113.5"))
            self.assertFalse(idx.is_first_seen_ip("alice", ""))
            self.assertFalse(idx.is_first_seen_ip("alice", "nope"))

    def test_first_seen_with_corrupted_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            # Inject a bogus iso string into the seen-cache directly;
            # we treat it as "unknown prefix" rather than trust it.
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso="not-a-date")
            # Because ts_iso > prior ("" for brand-new), the bogus
            # value is stored. is_first_seen_ip must fall back
            # safely when parse_iso returns None.
            self.assertTrue(idx.is_first_seen_ip("alice", "203.0.113.9"))


class ConcurrentSessionCountTests(unittest.TestCase):
    def test_counts_only_live_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, sessions = _make_index(tmp)
            sessions.create(owner_username="alice")
            sessions.create(owner_username="alice")
            sessions.create(owner_username="bob")
            self.assertEqual(idx.concurrent_session_count("alice"), 2)
            self.assertEqual(idx.concurrent_session_count("bob"), 1)
            self.assertEqual(idx.concurrent_session_count("carol"), 0)

    def test_ignores_expired_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, sessions = _make_index(tmp)
            # Short TTL, created in the past.
            sessions.create(owner_username="alice",
                            ttl_seconds=60, now=100.0)
            # One live session (created "now").
            sessions.create(owner_username="alice")
            # Let list_for filter the expired one.
            # Because SessionStore's list_for calls time.time() for
            # "now", the TTL-60 session created at 100.0 is long gone.
            self.assertEqual(idx.concurrent_session_count("alice"), 1)

    def test_empty_username_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            self.assertEqual(idx.concurrent_session_count(""), 0)


class ImpossibleTravelTests(unittest.TestCase):
    def test_distant_prefixes_within_window_is_anomalous(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            base = datetime.now(timezone.utc)
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_iso(base))
            idx.observe(username="alice", client_ip="198.51.100.5",
                        ts_iso=_iso(base + timedelta(minutes=2)))
            anomalous, detail = idx.anomaly_impossible_travel("alice")
            self.assertTrue(anomalous)
            self.assertIn("/16", detail)
            self.assertIn("->", detail)

    def test_same_prefix_is_not_anomalous(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            base = datetime.now(timezone.utc)
            # Both inside 203.0.0.0/16.
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_iso(base))
            idx.observe(username="alice", client_ip="203.0.200.9",
                        ts_iso=_iso(base + timedelta(minutes=1)))
            anomalous, detail = idx.anomaly_impossible_travel("alice")
            self.assertFalse(anomalous)
            self.assertEqual(detail, "")

    def test_outside_window_is_not_anomalous(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            base = datetime.now(timezone.utc)
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_iso(base))
            idx.observe(username="alice", client_ip="198.51.100.5",
                        ts_iso=_iso(base + timedelta(hours=2)))
            anomalous, _ = idx.anomaly_impossible_travel(
                "alice", window_minutes=15)
            self.assertFalse(anomalous)

    def test_single_login_is_not_anomalous(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_now_iso())
            anomalous, _ = idx.anomaly_impossible_travel("alice")
            self.assertFalse(anomalous)

    def test_no_logins_is_not_anomalous(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            anomalous, _ = idx.anomaly_impossible_travel("alice")
            self.assertFalse(anomalous)

    def test_ipv6_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            base = datetime.now(timezone.utc)
            idx.observe(username="alice", client_ip="2001:db8:1::1",
                        ts_iso=_iso(base))
            # Far outside 2001:db8::/32 — use a different /32.
            idx.observe(username="alice", client_ip="2606:4700::1",
                        ts_iso=_iso(base + timedelta(minutes=3)))
            anomalous, detail = idx.anomaly_impossible_travel("alice")
            self.assertTrue(anomalous)
            self.assertIn("/32", detail)

    def test_ipv6_same_isp_prefix_is_not_anomalous(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            base = datetime.now(timezone.utc)
            # Both inside 2001:db8::/32.
            idx.observe(username="alice", client_ip="2001:db8:1::1",
                        ts_iso=_iso(base))
            idx.observe(username="alice", client_ip="2001:db8:2::1",
                        ts_iso=_iso(base + timedelta(minutes=1)))
            anomalous, _ = idx.anomaly_impossible_travel("alice")
            self.assertFalse(anomalous)

    def test_window_minutes_must_be_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            base = datetime.now(timezone.utc)
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_iso(base))
            idx.observe(username="alice", client_ip="198.51.100.5",
                        ts_iso=_iso(base + timedelta(seconds=1)))
            self.assertEqual(
                idx.anomaly_impossible_travel("alice", window_minutes=0),
                (False, ""),
            )
            self.assertEqual(
                idx.anomaly_impossible_travel(""),
                (False, ""),
            )

    def test_corrupted_latest_timestamp_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso=_now_iso())
            # Latest has a non-parseable ts_iso; we bail out without
            # crashing and report "not anomalous".
            idx.observe(username="alice", client_ip="198.51.100.5",
                        ts_iso="nonsense")
            anomalous, _ = idx.anomaly_impossible_travel("alice")
            self.assertFalse(anomalous)

    def test_older_entries_with_bad_ts_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            base = datetime.now(timezone.utc)
            # First entry has a bad ts_iso; middle entry is good and
            # in-window but same /16 as latest; latest is valid.
            idx.observe(username="alice", client_ip="203.0.113.5",
                        ts_iso="bad-ts")
            idx.observe(username="alice", client_ip="203.0.200.5",
                        ts_iso=_iso(base))
            idx.observe(username="alice", client_ip="203.0.42.5",
                        ts_iso=_iso(base + timedelta(minutes=1)))
            anomalous, _ = idx.anomaly_impossible_travel("alice")
            self.assertFalse(anomalous)


class ThreadSafetyTests(unittest.TestCase):
    def test_concurrent_observe_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            stop = threading.Event()
            errors: list[BaseException] = []

            def writer(i: int) -> None:
                try:
                    for j in range(100):
                        if stop.is_set():
                            return
                        idx.observe(
                            username=f"user-{i % 3}",
                            client_ip=f"203.0.{i}.{j % 250 + 1}",
                            ts_iso=_now_iso(),
                        )
                except BaseException as exc:  # pragma: no cover
                    errors.append(exc)

            def reader(i: int) -> None:
                try:
                    for _ in range(100):
                        if stop.is_set():
                            return
                        idx.is_first_seen_ip(
                            f"user-{i % 3}", "203.0.113.5")
                        idx.anomaly_impossible_travel(f"user-{i % 3}")
                        idx.concurrent_session_count(f"user-{i % 3}")
                except BaseException as exc:  # pragma: no cover
                    errors.append(exc)

            threads: list[threading.Thread] = []
            for i in range(5):
                threads.append(threading.Thread(target=writer, args=(i,)))
                threads.append(threading.Thread(target=reader, args=(i,)))
            for t in threads:
                t.start()
            # Cap the run so a pathological lock regression can't hang
            # the suite.
            deadline = time.time() + 10.0
            for t in threads:
                t.join(timeout=max(0.0, deadline - time.time()))
            stop.set()
            for t in threads:
                t.join(timeout=1.0)
            self.assertFalse(errors, f"threaded errors: {errors}")
            # After all writers finished, at least one user is known.
            self.assertFalse(idx.is_first_seen_ip("user-0", "203.0.0.1"))


class ProtocolConformanceTests(unittest.TestCase):
    def test_index_is_login_history_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, _, _ = _make_index(tmp)
            self.assertIsInstance(idx, LoginHistoryProtocol)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
