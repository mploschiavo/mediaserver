"""Unit tests for ``services.security.session_aggregator``.

Covers the full public surface of the aggregator:

* Empty inputs produce an empty list.
* Controller-only sessions are tagged ``provider="controller"``.
* A single provider contributes rows tagged with its ``name``.
* Multiple providers merge, ordered by ``last_activity`` desc.
* ``list_for_user`` filters correctly.
* ``list_for_user`` is self-or-admin (non-admin can query self,
  cannot query another user).
* ``list_all`` is admin-only.
* A provider that raises is swallowed.
* ``first_seen_ip`` is enriched when a ``LoginHistoryIndex`` is
  wired; otherwise stays ``False``.
* ``device_class`` is derived via the classifier.
* ``SessionDTO.to_dict`` round-trips.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.authz import Actor, AuthorizationError  # noqa: E402
from media_stack.core.auth.users.provider import ExternalSession  # noqa: E402
from media_stack.services.security.session_aggregator import (  # noqa: E402
    CONTROLLER_PROVIDER,
    SessionAggregator,
    SessionDTO,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeControllerSession:
    """Structural stand-in for ``core.auth.session_store.Session``."""

    id: str
    owner_username: str
    created_at: str
    last_used_at: float = 0.0
    ip_prefix: str = ""
    device_class: str = ""
    user_agent: str = ""


class _FakeSessionStore:
    def __init__(
        self,
        sessions: list[_FakeControllerSession] | None = None,
        raise_on_list: bool = False,
    ) -> None:
        self._sessions = list(sessions or [])
        self._raise = raise_on_list

    def list_all_active(self) -> list[_FakeControllerSession]:
        if self._raise:
            raise RuntimeError("session store exploded")
        return list(self._sessions)

    def list_for(self, username: str) -> list[_FakeControllerSession]:
        if self._raise:
            raise RuntimeError("session store exploded")
        return [s for s in self._sessions if s.owner_username == username]


class _FakeSessionAdminProvider:
    """Structural impl of ``SessionAdminProvider``."""

    def __init__(
        self,
        name: str,
        sessions_by_user: dict[str, list[ExternalSession]] | None = None,
        raise_on_list: bool = False,
    ) -> None:
        self.name = name
        self._rows = sessions_by_user or {}
        self._raise = raise_on_list
        self.revoked: list[str] = []
        self.revoked_each: list[tuple[str, str]] = []

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        if self._raise:
            raise RuntimeError(f"{self.name} list blew up")
        return list(self._rows.get(external_id, []))

    def revoke_sessions(self, external_id: str) -> None:
        self.revoked.append(external_id)

    def revoke_session(self, external_id: str, session_id: str) -> None:
        self.revoked_each.append((external_id, session_id))


class _FakeLoginHistory:
    """Minimal impl of ``LoginHistoryProtocol``."""

    def __init__(
        self,
        first_seen: set[tuple[str, str]] | None = None,
        raise_on_query: bool = False,
    ) -> None:
        # Set of (username, ip) pairs that should return True.
        self._first_seen = first_seen or set()
        self._raise = raise_on_query
        self.calls: list[tuple[str, str]] = []

    def observe(self, *, username: str, client_ip: str, ts_iso: str) -> None:
        pass

    def is_first_seen_ip(
        self, username: str, client_ip: str, *,
        lookback_days: int = 90,
    ) -> bool:
        if self._raise:
            raise RuntimeError("history explode")
        self.calls.append((username, client_ip))
        return (username, client_ip) in self._first_seen

    def concurrent_session_count(self, username: str) -> int:
        return 0

    def anomaly_impossible_travel(
        self, username: str, *, window_minutes: int = 15,
    ) -> tuple[bool, str]:
        return False, ""


# ---------------------------------------------------------------------------
# Actor helpers
# ---------------------------------------------------------------------------


def _admin(username: str = "alice") -> Actor:
    return Actor(username=username, is_admin=True)


def _user(username: str = "bob") -> Actor:
    return Actor(username=username, is_admin=False)


# ---------------------------------------------------------------------------
# SessionDTO
# ---------------------------------------------------------------------------


class SessionDTOTests(unittest.TestCase):
    def test_to_dict_round_trip(self) -> None:
        d = SessionDTO(
            provider="jellyfin",
            session_id="jf-1",
            username="alice",
            device="Chrome on Mac",
            device_class="DESKTOP",
            client="chrome",
            client_ip="203.0.113.1",
            first_seen_ip=True,
            connected_since="2026-04-24T00:00:00Z",
            last_activity="2026-04-24T01:00:00Z",
            revokable=False,
        )
        self.assertEqual(d.to_dict(), {
            "provider": "jellyfin",
            "session_id": "jf-1",
            "username": "alice",
            "device": "Chrome on Mac",
            "device_class": "DESKTOP",
            "client": "chrome",
            "client_ip": "203.0.113.1",
            "first_seen_ip": True,
            "connected_since": "2026-04-24T00:00:00Z",
            "last_activity": "2026-04-24T01:00:00Z",
            "revokable": False,
        })

    def test_defaults(self) -> None:
        d = SessionDTO(
            provider="controller", session_id="s-1", username="alice",
        )
        self.assertEqual(d.device, "")
        self.assertEqual(d.device_class, "")
        self.assertFalse(d.first_seen_ip)
        self.assertTrue(d.revokable)


# ---------------------------------------------------------------------------
# SessionAggregator.__init__
# ---------------------------------------------------------------------------


class AggregatorInitTests(unittest.TestCase):
    def test_requires_session_store(self) -> None:
        with self.assertRaises(ValueError):
            SessionAggregator(session_store=None)  # type: ignore[arg-type]

    def test_rejects_non_provider(self) -> None:
        class _NotAProvider:
            # Missing .name and the revoke methods — fails the
            # runtime_checkable isinstance(p, SessionAdminProvider).
            pass
        with self.assertRaises(TypeError):
            SessionAggregator(
                session_store=_FakeSessionStore(),
                providers=[_NotAProvider()],  # type: ignore[list-item]
            )

    def test_accepts_structural_provider(self) -> None:
        # No raise.
        SessionAggregator(
            session_store=_FakeSessionStore(),
            providers=[_FakeSessionAdminProvider("jellyfin")],
        )


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


class ListAllTests(unittest.TestCase):
    def test_empty_inputs_empty_list(self) -> None:
        agg = SessionAggregator(session_store=_FakeSessionStore())
        self.assertEqual(agg.list_all(actor=_admin()), [])

    def test_controller_only_tagged_controller(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
                last_used_at=1_700_000_000.0,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64)",
                device_class="DESKTOP", ip_prefix="203.0.113.0/24",
            ),
        ])
        agg = SessionAggregator(session_store=store)
        rows = agg.list_all(actor=_admin())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, CONTROLLER_PROVIDER)
        self.assertEqual(rows[0].session_id, "cs-1")
        self.assertEqual(rows[0].username, "alice")
        self.assertEqual(rows[0].device_class, "DESKTOP")
        # last_activity was translated from the float epoch.
        self.assertTrue(rows[0].last_activity.endswith("Z"))

    def test_single_provider_rows_tagged_with_name(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z", last_used_at=100.0,
            ),
        ])
        jelly = _FakeSessionAdminProvider("jellyfin", sessions_by_user={
            "alice": [ExternalSession(
                session_id="jf-x", device="Chromecast",
                client="Jellyfin for Android TV",
                last_activity="2026-04-24T02:00:00Z",
                ip="10.0.0.5",
            )],
        })
        agg = SessionAggregator(session_store=store, providers=[jelly])
        rows = agg.list_all(actor=_admin())
        providers = sorted({r.provider for r in rows})
        self.assertEqual(providers, ["controller", "jellyfin"])
        jf_row = next(r for r in rows if r.provider == "jellyfin")
        self.assertEqual(jf_row.username, "alice")
        self.assertEqual(jf_row.session_id, "jf-x")

    def test_multiple_providers_merged_ordered_by_last_activity_desc(
        self,
    ) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z", last_used_at=100.0,
            ),
        ])
        jelly = _FakeSessionAdminProvider("jellyfin", sessions_by_user={
            "alice": [ExternalSession(
                session_id="jf-mid",
                last_activity="2026-04-24T02:00:00Z",
            )],
        })
        authelia = _FakeSessionAdminProvider("authelia", sessions_by_user={
            "alice": [
                ExternalSession(
                    session_id="au-new",
                    last_activity="2026-04-24T05:00:00Z",
                ),
                ExternalSession(
                    session_id="au-old",
                    last_activity="2026-04-24T01:00:00Z",
                ),
            ],
        })
        agg = SessionAggregator(
            session_store=store, providers=[jelly, authelia],
        )
        rows = agg.list_all(actor=_admin())
        ids = [r.session_id for r in rows]
        # Ordering is by last_activity desc.
        self.assertEqual(ids[0], "au-new")
        self.assertIn("jf-mid", ids)
        self.assertIn("au-old", ids)
        # au-new is before au-old
        self.assertLess(ids.index("au-new"), ids.index("au-old"))

    def test_list_all_requires_admin(self) -> None:
        agg = SessionAggregator(session_store=_FakeSessionStore())
        with self.assertRaises(AuthorizationError) as ctx:
            agg.list_all(actor=_user())
        self.assertEqual(ctx.exception.reason, "admin_required")

    def test_provider_raises_is_swallowed(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
            ),
        ])
        good = _FakeSessionAdminProvider("jellyfin", sessions_by_user={
            "alice": [ExternalSession(
                session_id="jf-1", last_activity="2026-04-24T05:00:00Z",
            )],
        })
        bad = _FakeSessionAdminProvider("authelia", raise_on_list=True)
        agg = SessionAggregator(
            session_store=store, providers=[good, bad],
        )
        rows = agg.list_all(actor=_admin())
        providers = sorted({r.provider for r in rows})
        # authelia provider contributed nothing, but controller + jellyfin
        # are intact.
        self.assertEqual(providers, ["controller", "jellyfin"])

    def test_controller_store_raises_yields_empty_controller_rows(
        self,
    ) -> None:
        store = _FakeSessionStore(raise_on_list=True)
        agg = SessionAggregator(session_store=store)
        # No rows, no exception.
        self.assertEqual(agg.list_all(actor=_admin()), [])

    def test_first_seen_ip_enriched(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
                ip_prefix="203.0.113.7", last_used_at=100.0,
            ),
        ])
        history = _FakeLoginHistory(first_seen={("alice", "203.0.113.7")})
        agg = SessionAggregator(
            session_store=store, login_history=history,
        )
        rows = agg.list_all(actor=_admin())
        self.assertTrue(rows[0].first_seen_ip)

    def test_first_seen_ip_false_when_not_in_history(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
                ip_prefix="10.0.0.1", last_used_at=100.0,
            ),
        ])
        history = _FakeLoginHistory(first_seen=set())
        agg = SessionAggregator(
            session_store=store, login_history=history,
        )
        rows = agg.list_all(actor=_admin())
        self.assertFalse(rows[0].first_seen_ip)

    def test_first_seen_ip_false_without_history(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
                ip_prefix="10.0.0.1", last_used_at=100.0,
            ),
        ])
        agg = SessionAggregator(session_store=store)
        rows = agg.list_all(actor=_admin())
        self.assertFalse(rows[0].first_seen_ip)

    def test_first_seen_ip_history_raises_is_swallowed(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
                ip_prefix="10.0.0.1", last_used_at=100.0,
            ),
        ])
        history = _FakeLoginHistory(raise_on_query=True)
        agg = SessionAggregator(
            session_store=store, login_history=history,
        )
        rows = agg.list_all(actor=_admin())
        self.assertFalse(rows[0].first_seen_ip)

    def test_device_class_derived_via_classifier(self) -> None:
        """When the stored ``device_class`` is empty, the injected
        classifier derives it from the UA."""
        captured: list[str] = []

        def fake_classify(source: str) -> str:
            captured.append(source)
            return "TV" if "tv" in source.lower() else "UNKNOWN"

        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
                user_agent="JellyfinTV/1.0", last_used_at=100.0,
            ),
        ])
        agg = SessionAggregator(
            session_store=store, device_classifier=fake_classify,
        )
        rows = agg.list_all(actor=_admin())
        # Controller row has a non-empty stored class? We set it blank
        # above, so the classifier was consulted.
        self.assertEqual(rows[0].device_class, "TV")
        self.assertIn("JellyfinTV/1.0", captured)

    def test_device_class_for_provider_uses_classifier(self) -> None:
        """Provider rows don't carry a ``device_class`` field on
        ``ExternalSession``; the aggregator must derive one via the
        classifier from ``client``."""
        classifier_calls: list[str] = []

        def fake_classify(source: str) -> str:
            classifier_calls.append(source)
            return "PHONE" if "iphone" in source.lower() else "DESKTOP"

        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
            ),
        ])
        jelly = _FakeSessionAdminProvider("jellyfin", sessions_by_user={
            "alice": [ExternalSession(
                session_id="jf-1", device="iPhone 15",
                client="Jellyfin Mobile (iPhone)",
                last_activity="2026-04-24T02:00:00Z",
            )],
        })
        agg = SessionAggregator(
            session_store=store, providers=[jelly],
            device_classifier=fake_classify,
        )
        rows = agg.list_all(actor=_admin())
        jf_row = next(r for r in rows if r.provider == "jellyfin")
        self.assertEqual(jf_row.device_class, "PHONE")
        self.assertTrue(any("Jellyfin Mobile" in s for s in classifier_calls))

    def test_dedup_on_provider_session_id_pair(self) -> None:
        """Two identical provider rows for the same user collapse
        to one DTO (first-wins by insertion order)."""
        store = _FakeSessionStore()  # no controller sessions
        # Two providers with same name aren't allowed (dict key
        # collision). But the same session_id in the same provider
        # can legitimately appear twice via retries; the aggregator
        # dedups.
        jelly = _FakeSessionAdminProvider("jellyfin", sessions_by_user={
            "alice": [
                ExternalSession(session_id="dup", last_activity="t1"),
                ExternalSession(session_id="dup", last_activity="t2"),
            ],
        })
        # Need alice to be "known" for provider fan-out.
        store2 = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z",
            ),
        ])
        agg = SessionAggregator(session_store=store2, providers=[jelly])
        rows = agg.list_all(actor=_admin())
        jf_rows = [r for r in rows if r.provider == "jellyfin"]
        self.assertEqual(len(jf_rows), 1)


# ---------------------------------------------------------------------------
# list_for_user
# ---------------------------------------------------------------------------


class ListForUserTests(unittest.TestCase):
    def test_filters_by_username(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="a-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z", last_used_at=1.0,
            ),
            _FakeControllerSession(
                id="b-1", owner_username="bob",
                created_at="2026-04-24T00:00:00Z", last_used_at=2.0,
            ),
        ])
        agg = SessionAggregator(session_store=store)
        rows = agg.list_for_user(username="alice", actor=_admin())
        self.assertEqual([r.session_id for r in rows], ["a-1"])

    def test_self_can_query_own(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="b-1", owner_username="bob",
                created_at="2026-04-24T00:00:00Z",
            ),
        ])
        agg = SessionAggregator(session_store=store)
        rows = agg.list_for_user(username="bob", actor=_user("bob"))
        self.assertEqual([r.session_id for r in rows], ["b-1"])

    def test_non_admin_cannot_query_other_user(self) -> None:
        agg = SessionAggregator(session_store=_FakeSessionStore())
        with self.assertRaises(AuthorizationError) as ctx:
            agg.list_for_user(username="alice", actor=_user("bob"))
        self.assertEqual(ctx.exception.reason, "self_or_admin_required")

    def test_admin_can_query_any_user(self) -> None:
        agg = SessionAggregator(session_store=_FakeSessionStore())
        # No exception, even for a user with no sessions.
        self.assertEqual(
            agg.list_for_user(username="somebody", actor=_admin()), [],
        )

    def test_empty_username_returns_empty(self) -> None:
        agg = SessionAggregator(session_store=_FakeSessionStore())
        self.assertEqual(
            agg.list_for_user(username="", actor=_admin()), [],
        )

    def test_merges_controller_and_provider(self) -> None:
        store = _FakeSessionStore(sessions=[
            _FakeControllerSession(
                id="cs-1", owner_username="alice",
                created_at="2026-04-24T00:00:00Z", last_used_at=100.0,
            ),
        ])
        jelly = _FakeSessionAdminProvider("jellyfin", sessions_by_user={
            "alice": [ExternalSession(
                session_id="jf-1", last_activity="2026-04-24T05:00:00Z",
            )],
        })
        agg = SessionAggregator(
            session_store=store, providers=[jelly],
        )
        rows = agg.list_for_user(username="alice", actor=_admin())
        providers = {r.provider for r in rows}
        self.assertEqual(providers, {"controller", "jellyfin"})

    def test_list_for_user_swallows_store_error(self) -> None:
        store = _FakeSessionStore(raise_on_list=True)
        agg = SessionAggregator(session_store=store)
        self.assertEqual(
            agg.list_for_user(username="alice", actor=_admin()), [],
        )


if __name__ == "__main__":
    unittest.main()
