"""Unit tests for ``services.security.security_report_service``.

Covers:

* ``failed_login_clusters`` — groups by /24, respects
  ``min_attempts``, empty input, ``since_hours`` cutoff.
* ``new_location_alerts`` — delegates to the login-history index.
* ``concurrent_session_spikes`` — only users at or above threshold.
* ``login_history_for_user`` — returns the user's audit entries
  newest-first up to ``limit``.
* All four entry points are ``@requires_admin``.
* Every value-object dataclass round-trips via ``to_dict``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.authz import Actor, AuthorizationError  # noqa: E402
from media_stack.core.auth.users.audit_actions import (  # noqa: E402
    LOGIN_BLOCKED,
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
)
from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402
from media_stack.services.security import security_report_service  # noqa: E402
from media_stack.services.security.security_report_service import (  # noqa: E402
    ConcurrentSessionAlert,
    FailedLoginCluster,
    NewLocationAlert,
    SecurityReportService,
)
from media_stack.services.security.session_aggregator import (  # noqa: E402
    SessionAggregator,
    SessionDTO,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLoginHistory:
    """Minimal impl of ``LoginHistoryProtocol`` suitable for the report
    service's use sites."""

    def __init__(
        self,
        first_seen: set[tuple[str, str]] | None = None,
        raise_on_query: bool = False,
    ) -> None:
        self._first_seen = first_seen or set()
        self._raise = raise_on_query
        self.calls: list[tuple[str, str, int]] = []

    def observe(self, *, username: str, client_ip: str, ts_iso: str) -> None:
        pass

    def is_first_seen_ip(
        self, username: str, client_ip: str, *,
        lookback_days: int = 90,
    ) -> bool:
        if self._raise:
            raise RuntimeError("history boom")
        self.calls.append((username, client_ip, lookback_days))
        return (username, client_ip) in self._first_seen

    def concurrent_session_count(self, username: str) -> int:
        return 0

    def anomaly_impossible_travel(
        self, username: str, *, window_minutes: int = 15,
    ) -> tuple[bool, str]:
        return False, ""


class _FakeAggregator:
    """Stand-in for ``SessionAggregator`` that returns a canned list
    regardless of the caller's actor — the report service only
    looks at the rows, not the authz chain (its own authz is
    enforced by ``@requires_admin``)."""

    def __init__(self, rows: list[SessionDTO] | None = None,
                 raise_on_list: bool = False) -> None:
        self._rows = list(rows or [])
        self._raise = raise_on_list
        self.calls = 0

    def list_all(self, *, actor: Actor) -> list[SessionDTO]:
        self.calls += 1
        if self._raise:
            raise RuntimeError("aggregator exploded")
        return list(self._rows)

    def list_for_user(
        self, *, username: str, actor: Actor,
    ) -> list[SessionDTO]:
        return [r for r in self._rows if r.username == username]


def _dto(
    username: str,
    session_id: str,
    provider: str = "controller",
    last_activity: str = "2026-04-24T10:00:00.000000Z",
) -> SessionDTO:
    return SessionDTO(
        provider=provider, session_id=session_id, username=username,
        last_activity=last_activity,
    )


def _admin(username: str = "alice") -> Actor:
    return Actor(username=username, is_admin=True)


def _user(username: str = "bob") -> Actor:
    return Actor(username=username, is_admin=False)


# ---------------------------------------------------------------------------
# Audit-log helpers
# ---------------------------------------------------------------------------


def _fresh_audit() -> AuditLog:
    """A real AuditLog backed by a tempfile — the in-memory iter_entries
    path works the same as production. We write directly via ``append``
    which stamps the current wall-clock; tests don't rely on that for
    ordering, only on grouping."""
    tmpdir = tempfile.TemporaryDirectory()
    # Keep the TemporaryDirectory alive for the life of the log by
    # stashing it on the instance.
    audit = AuditLog(Path(tmpdir.name) / "audit.jsonl")
    audit._tmpdir_keepalive = tmpdir  # type: ignore[attr-defined]
    return audit


def _seed_failure(audit: AuditLog, username: str, ip: str) -> None:
    audit.append(
        actor=username or "anonymous",
        action=LOGIN_FAILURE,
        target=username or "unknown",
        result="fail",
        ip=ip,
        user_agent="",
        detail={"reason": "bad_credentials", "provider": "controller"},
    )


def _seed_success(audit: AuditLog, username: str, ip: str,
                  provider: str = "controller") -> None:
    audit.append(
        actor=username, action=LOGIN_SUCCESS, target=username, result="ok",
        ip=ip, user_agent="Mozilla/5.0",
        detail={"provider": provider},
    )


# ---------------------------------------------------------------------------
# Dataclass round-trips
# ---------------------------------------------------------------------------


class CutoffHelperTests(unittest.TestCase):
    """Direct tests of the private ``_cutoff_iso`` helper — it
    implements the audit-log-compatible format contract that every
    report method relies on for correct ``since`` filtering."""

    def test_zero_hours_matches_audit_format(self) -> None:
        iso = security_report_service._cutoff_iso(hours=0)
        # Audit log writes isoformat(timespec="seconds") which ends
        # in "+00:00" (no fractional seconds, no Z).
        self.assertTrue(iso.endswith("+00:00"), iso)
        self.assertNotIn(".", iso)

    def test_non_zero_hours_goes_back(self) -> None:
        now = security_report_service._cutoff_iso(hours=0)
        past = security_report_service._cutoff_iso(hours=1)
        self.assertLess(past, now)


class DataclassRoundTripTests(unittest.TestCase):
    def test_failed_login_cluster_round_trip(self) -> None:
        c = FailedLoginCluster(
            ip_prefix="203.0.113.0/24",
            usernames=("alice", "bob"),
            attempt_count=7,
            first_seen="2026-04-24T09:00:00+00:00",
            last_seen="2026-04-24T10:00:00+00:00",
        )
        self.assertEqual(c.to_dict(), {
            "ip_prefix": "203.0.113.0/24",
            "usernames": ["alice", "bob"],
            "attempt_count": 7,
            "first_seen": "2026-04-24T09:00:00+00:00",
            "last_seen": "2026-04-24T10:00:00+00:00",
        })

    def test_new_location_alert_round_trip(self) -> None:
        a = NewLocationAlert(
            username="alice", ip_prefix="203.0.113.0/24",
            observed_at="2026-04-24T10:00:00+00:00",
            provider="controller",
        )
        self.assertEqual(a.to_dict(), {
            "username": "alice",
            "ip_prefix": "203.0.113.0/24",
            "observed_at": "2026-04-24T10:00:00+00:00",
            "provider": "controller",
        })

    def test_concurrent_session_alert_round_trip(self) -> None:
        a = ConcurrentSessionAlert(
            username="alice", count=6, threshold=5,
            providers=("controller", "jellyfin"),
        )
        self.assertEqual(a.to_dict(), {
            "username": "alice", "count": 6, "threshold": 5,
            "providers": ["controller", "jellyfin"],
        })


# ---------------------------------------------------------------------------
# __init__ guards
# ---------------------------------------------------------------------------


class ServiceInitTests(unittest.TestCase):
    def test_requires_audit(self) -> None:
        with self.assertRaises(ValueError):
            SecurityReportService(
                audit_log=None,  # type: ignore[arg-type]
                session_aggregator=_FakeAggregator(),
                login_history=_FakeLoginHistory(),
            )

    def test_requires_aggregator(self) -> None:
        with self.assertRaises(ValueError):
            SecurityReportService(
                audit_log=_fresh_audit(),
                session_aggregator=None,  # type: ignore[arg-type]
                login_history=_FakeLoginHistory(),
            )

    def test_requires_login_history(self) -> None:
        with self.assertRaises(ValueError):
            SecurityReportService(
                audit_log=_fresh_audit(),
                session_aggregator=_FakeAggregator(),
                login_history=None,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# failed_login_clusters
# ---------------------------------------------------------------------------


class FailedLoginClustersTests(unittest.TestCase):
    def _service(self, audit: AuditLog) -> SecurityReportService:
        return SecurityReportService(
            audit_log=audit,
            session_aggregator=_FakeAggregator(),
            login_history=_FakeLoginHistory(),
        )

    def test_empty_input_empty_list(self) -> None:
        svc = self._service(_fresh_audit())
        self.assertEqual(
            svc.failed_login_clusters(actor=_admin()), [],
        )

    def test_groups_by_24(self) -> None:
        audit = _fresh_audit()
        # 6 attempts at 203.0.113.x (/24)
        for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3",
                   "203.0.113.4", "203.0.113.5", "203.0.113.6"):
            _seed_failure(audit, "alice", ip)
        # 1 attempt from a wholly different /24 — below default
        # min_attempts (5), should be filtered out.
        _seed_failure(audit, "bob", "10.0.0.1")
        svc = self._service(audit)
        clusters = svc.failed_login_clusters(actor=_admin())
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].ip_prefix, "203.0.113.0/24")
        self.assertEqual(clusters[0].attempt_count, 6)
        self.assertIn("alice", clusters[0].usernames)

    def test_respects_min_attempts(self) -> None:
        audit = _fresh_audit()
        for ip in ("203.0.113.1", "203.0.113.2"):
            _seed_failure(audit, "alice", ip)
        svc = self._service(audit)
        # Default min_attempts=5 filters out our 2-attempt cluster.
        self.assertEqual(
            svc.failed_login_clusters(actor=_admin()), [],
        )
        # Explicit override surfaces it.
        rows = svc.failed_login_clusters(
            actor=_admin(), min_attempts=1,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].attempt_count, 2)

    def test_since_hours_cutoff_respected(self) -> None:
        """``since_hours`` narrows the audit scan window.

        Our default (24h) includes entries written seconds ago; a
        negative-style cutoff (future window via a huge value) is
        hard to test portably. We verify the two branches: the
        default surfaces the cluster, and feeding a ``since``
        parameter through the path doesn't drop entries inside it.
        """
        audit = _fresh_audit()
        for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3",
                   "10.0.0.4", "10.0.0.5", "10.0.0.6"):
            _seed_failure(audit, "alice", ip)
        svc = self._service(audit)
        # Default (24h) includes everything.
        self.assertGreater(
            len(svc.failed_login_clusters(actor=_admin())), 0,
        )
        # Huge lookback => still everything, proves the path is
        # alive (and not accidentally returning [] on any non-zero).
        self.assertGreater(
            len(svc.failed_login_clusters(
                actor=_admin(), since_hours=24 * 365,
            )), 0,
        )

    def test_requires_admin(self) -> None:
        svc = self._service(_fresh_audit())
        with self.assertRaises(AuthorizationError) as ctx:
            svc.failed_login_clusters(actor=_user())
        self.assertEqual(ctx.exception.reason, "admin_required")

    def test_non_login_failure_events_ignored(self) -> None:
        audit = _fresh_audit()
        # LOGIN_BLOCKED entries should NOT count toward the failure
        # cluster — they are rate-limit bans, not credential guesses.
        for _ in range(10):
            audit.append(
                actor="alice", action=LOGIN_BLOCKED, target="alice",
                result="fail", ip="203.0.113.1", detail={"reason": "ban"},
            )
        svc = self._service(audit)
        self.assertEqual(
            svc.failed_login_clusters(actor=_admin()), [],
        )

    def test_sorted_by_attempt_count_desc(self) -> None:
        audit = _fresh_audit()
        for ip in ("10.0.0.1",) * 10:
            _seed_failure(audit, "alice", ip)
        for ip in ("203.0.113.1",) * 6:
            _seed_failure(audit, "bob", ip)
        svc = self._service(audit)
        rows = svc.failed_login_clusters(actor=_admin())
        self.assertEqual([r.attempt_count for r in rows], [10, 6])


# ---------------------------------------------------------------------------
# new_location_alerts
# ---------------------------------------------------------------------------


class NewLocationAlertsTests(unittest.TestCase):
    def _service(
        self, audit: AuditLog, history: _FakeLoginHistory,
    ) -> SecurityReportService:
        return SecurityReportService(
            audit_log=audit,
            session_aggregator=_FakeAggregator(),
            login_history=history,
        )

    def test_empty(self) -> None:
        svc = self._service(_fresh_audit(), _FakeLoginHistory())
        self.assertEqual(svc.new_location_alerts(actor=_admin()), [])

    def test_flags_unknown_prefix_via_history(self) -> None:
        audit = _fresh_audit()
        _seed_success(audit, "alice", "203.0.113.1")
        _seed_success(audit, "alice", "10.0.0.5")
        history = _FakeLoginHistory(
            first_seen={("alice", "203.0.113.1")},
        )
        svc = self._service(audit, history)
        rows = svc.new_location_alerts(actor=_admin())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].username, "alice")
        self.assertEqual(rows[0].ip_prefix, "203.0.113.0/24")
        # Lookback default passed through.
        # calls stores (user, ip, lookback_days); two attempts (one per login)
        self.assertTrue(
            any(c[2] == 90 for c in history.calls),
            f"expected default lookback 90 in {history.calls!r}",
        )

    def test_respects_lookback_days(self) -> None:
        audit = _fresh_audit()
        _seed_success(audit, "alice", "203.0.113.1")
        history = _FakeLoginHistory(
            first_seen={("alice", "203.0.113.1")},
        )
        svc = self._service(audit, history)
        svc.new_location_alerts(actor=_admin(), lookback_days=30)
        self.assertEqual(history.calls[-1][2], 30)

    def test_requires_admin(self) -> None:
        svc = self._service(_fresh_audit(), _FakeLoginHistory())
        with self.assertRaises(AuthorizationError) as ctx:
            svc.new_location_alerts(actor=_user())
        self.assertEqual(ctx.exception.reason, "admin_required")

    def test_dedups_same_pair(self) -> None:
        audit = _fresh_audit()
        # Two successful logins, same (user, /24) — should emit one alert.
        _seed_success(audit, "alice", "203.0.113.1")
        _seed_success(audit, "alice", "203.0.113.2")  # same /24
        history = _FakeLoginHistory(
            first_seen={("alice", "203.0.113.1"), ("alice", "203.0.113.2")},
        )
        svc = self._service(audit, history)
        rows = svc.new_location_alerts(actor=_admin())
        self.assertEqual(len(rows), 1)

    def test_history_raises_is_swallowed(self) -> None:
        audit = _fresh_audit()
        _seed_success(audit, "alice", "203.0.113.1")
        history = _FakeLoginHistory(raise_on_query=True)
        svc = self._service(audit, history)
        self.assertEqual(svc.new_location_alerts(actor=_admin()), [])

    def test_carries_provider_from_detail(self) -> None:
        audit = _fresh_audit()
        _seed_success(audit, "alice", "203.0.113.1", provider="jellyfin")
        history = _FakeLoginHistory(first_seen={("alice", "203.0.113.1")})
        svc = self._service(audit, history)
        rows = svc.new_location_alerts(actor=_admin())
        self.assertEqual(rows[0].provider, "jellyfin")


# ---------------------------------------------------------------------------
# concurrent_session_spikes
# ---------------------------------------------------------------------------


class ConcurrentSessionSpikesTests(unittest.TestCase):
    def _service(self, aggregator) -> SecurityReportService:
        return SecurityReportService(
            audit_log=_fresh_audit(),
            session_aggregator=aggregator,
            login_history=_FakeLoginHistory(),
        )

    def test_only_users_over_threshold(self) -> None:
        rows = [
            # alice: 5 sessions spread across providers
            _dto("alice", "a1", "controller"),
            _dto("alice", "a2", "controller"),
            _dto("alice", "a3", "jellyfin"),
            _dto("alice", "a4", "jellyfin"),
            _dto("alice", "a5", "authelia"),
            # bob: 2 sessions
            _dto("bob", "b1", "controller"),
            _dto("bob", "b2", "jellyfin"),
        ]
        svc = self._service(_FakeAggregator(rows))
        alerts = svc.concurrent_session_spikes(actor=_admin(), threshold=5)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].username, "alice")
        self.assertEqual(alerts[0].count, 5)
        self.assertEqual(
            alerts[0].providers,
            ("authelia", "controller", "jellyfin"),
        )

    def test_threshold_below_one_clamped(self) -> None:
        rows = [_dto("alice", "a1")]
        svc = self._service(_FakeAggregator(rows))
        # threshold=0 is clamped to 1 — alice with 1 session should show.
        alerts = svc.concurrent_session_spikes(actor=_admin(), threshold=0)
        self.assertEqual(len(alerts), 1)

    def test_ignores_anonymous_sessions(self) -> None:
        rows = [
            _dto("", "x1"), _dto("", "x2"), _dto("", "x3"),
        ]
        svc = self._service(_FakeAggregator(rows))
        self.assertEqual(
            svc.concurrent_session_spikes(actor=_admin(), threshold=1), [],
        )

    def test_aggregator_raises_yields_empty(self) -> None:
        svc = self._service(_FakeAggregator(raise_on_list=True))
        self.assertEqual(
            svc.concurrent_session_spikes(actor=_admin()), [],
        )

    def test_requires_admin(self) -> None:
        svc = self._service(_FakeAggregator())
        with self.assertRaises(AuthorizationError) as ctx:
            svc.concurrent_session_spikes(actor=_user())
        self.assertEqual(ctx.exception.reason, "admin_required")

    def test_sorted_count_desc(self) -> None:
        rows = [
            _dto("alice", "a1"), _dto("alice", "a2"),
            _dto("alice", "a3"),
            _dto("bob", "b1"), _dto("bob", "b2"),
            _dto("bob", "b3"), _dto("bob", "b4"),
        ]
        svc = self._service(_FakeAggregator(rows))
        alerts = svc.concurrent_session_spikes(actor=_admin(), threshold=2)
        self.assertEqual([a.username for a in alerts], ["bob", "alice"])


# ---------------------------------------------------------------------------
# login_history_for_user
# ---------------------------------------------------------------------------


class LoginHistoryForUserTests(unittest.TestCase):
    def _service(self, audit: AuditLog) -> SecurityReportService:
        return SecurityReportService(
            audit_log=audit,
            session_aggregator=_FakeAggregator(),
            login_history=_FakeLoginHistory(),
        )

    def test_filters_by_username(self) -> None:
        audit = _fresh_audit()
        _seed_success(audit, "alice", "10.0.0.1")
        _seed_failure(audit, "bob", "10.0.0.2")
        _seed_success(audit, "alice", "10.0.0.3")
        svc = self._service(audit)
        rows = svc.login_history_for_user(username="alice", actor=_admin())
        for e in rows:
            self.assertTrue(
                e.get("target") == "alice" or e.get("actor") == "alice",
                e,
            )
        # Bob's failure is filtered out.
        self.assertFalse(
            any(e.get("target") == "bob" for e in rows),
        )

    def test_newest_first(self) -> None:
        audit = _fresh_audit()
        _seed_success(audit, "alice", "10.0.0.1")
        _seed_success(audit, "alice", "10.0.0.2")
        _seed_success(audit, "alice", "10.0.0.3")
        svc = self._service(audit)
        rows = svc.login_history_for_user(username="alice", actor=_admin())
        # Appended in order 1,2,3 — newest-first means 3 comes first.
        self.assertEqual(rows[0]["ip"], "10.0.0.3")
        self.assertEqual(rows[-1]["ip"], "10.0.0.1")

    def test_respects_limit(self) -> None:
        audit = _fresh_audit()
        for i in range(5):
            _seed_success(audit, "alice", f"10.0.0.{i}")
        svc = self._service(audit)
        rows = svc.login_history_for_user(
            username="alice", actor=_admin(), limit=2,
        )
        self.assertEqual(len(rows), 2)

    def test_empty_username_returns_empty(self) -> None:
        svc = self._service(_fresh_audit())
        self.assertEqual(
            svc.login_history_for_user(username="", actor=_admin()), [],
        )

    def test_requires_admin(self) -> None:
        svc = self._service(_fresh_audit())
        with self.assertRaises(AuthorizationError) as ctx:
            svc.login_history_for_user(username="alice", actor=_user())
        self.assertEqual(ctx.exception.reason, "admin_required")

    def test_limit_below_one_still_returns_at_least_one(self) -> None:
        """``limit=0`` is clamped to 1 (``max(1, ...)``) — defensive
        against caller bugs that would otherwise silently return
        zero and mask a misfiled event."""
        audit = _fresh_audit()
        _seed_success(audit, "alice", "10.0.0.1")
        svc = self._service(audit)
        rows = svc.login_history_for_user(
            username="alice", actor=_admin(), limit=0,
        )
        self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# requires_admin ratchet
# ---------------------------------------------------------------------------


class AuthzRatchetTests(unittest.TestCase):
    """Every public method on the service must carry the admin gate."""

    def test_all_public_methods_require_admin(self) -> None:
        methods = [
            SecurityReportService.failed_login_clusters,
            SecurityReportService.new_location_alerts,
            SecurityReportService.concurrent_session_spikes,
            SecurityReportService.login_history_for_user,
        ]
        for m in methods:
            marker = getattr(m, "__authz__", "")
            self.assertEqual(
                marker, "requires_admin",
                f"{m.__name__} missing @requires_admin (got {marker!r})",
            )


# ---------------------------------------------------------------------------
# Integration-ish: end-to-end with a real SessionAggregator
# ---------------------------------------------------------------------------


class EndToEndTests(unittest.TestCase):
    """A tiny smoke test using the real ``SessionAggregator`` against
    a hand-rolled session store — catches integration breakage
    between the two services without standing up the full stack."""

    def test_spikes_via_real_aggregator(self) -> None:
        from dataclasses import dataclass as _dc

        @_dc
        class _CS:
            id: str
            owner_username: str
            created_at: str
            last_used_at: float = 0.0
            ip_prefix: str = ""
            device_class: str = ""
            user_agent: str = ""

        class _SS:
            def __init__(self) -> None:
                self._rows: list[_CS] = [
                    _CS(id=f"cs-{i}", owner_username="alice",
                        created_at="2026-04-24T00:00:00Z",
                        last_used_at=float(i))
                    for i in range(5)
                ]

            def list_all_active(self) -> list[_CS]:
                return list(self._rows)

            def list_for(self, username: str) -> list[_CS]:
                return [r for r in self._rows
                        if r.owner_username == username]

        aggregator = SessionAggregator(session_store=_SS())
        svc = SecurityReportService(
            audit_log=_fresh_audit(),
            session_aggregator=aggregator,
            login_history=_FakeLoginHistory(),
        )
        alerts = svc.concurrent_session_spikes(actor=_admin(), threshold=5)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].username, "alice")
        self.assertEqual(alerts[0].count, 5)


if __name__ == "__main__":
    unittest.main()
