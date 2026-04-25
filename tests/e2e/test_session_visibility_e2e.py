"""End-to-end coverage for the session-visibility POST flows.

Runs against the in-process :class:`PostRequestHandler` with stubbed
providers. Avoids Docker so the test is runnable everywhere CI runs.

Scenarios covered:

1. Admin logs in (simulated actor) → lists sessions via the store →
   revokes one by id → list reflects the kill.
2. Admin creates a user ban → Authelia stub's ``disable_user`` fires
   with the mapped external id → remove cascades ``enable_user``.
3. Admin creates an IP ban → ``add_ip_deny`` fires on the stub
   provider.
4. User's session fires ``/api/me/revoke-others`` → sees everybody
   else's session on the same account killed.
5. Emergency revoke returns 200 + every session from step 1 is gone.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.handlers_post import (  # noqa: E402
    PostRequestHandler, _global_post_limiter, _user_mgmt_limiter,
)
from media_stack.api.services import security_post_handlers as sph  # noqa: E402
from media_stack.core.auth.authz import Actor  # noqa: E402
from media_stack.core.auth.idempotency_cache import (  # noqa: E402
    IdempotencyCache, IdempotencyCacheRegistry,
)
from media_stack.core.auth.session_store import SessionStore  # noqa: E402
from media_stack.core.events.bus import EventBus  # noqa: E402


def _handler(path: str, body: dict | None = None, *,
             idem: str = "", cookie: str = ""):
    h = MagicMock()
    h.path = path
    h.client_address = ("127.0.0.1", 0)
    h._read_json_body.return_value = body or {}
    hmap = {"Cookie": cookie, "X-CSRF-Token": "", "Idempotency-Key": idem,
            "Origin": "", "Host": "localhost"}
    h.headers = MagicMock()
    h.headers.get.side_effect = lambda k, d="": hmap.get(k, d)
    captured: dict = {}

    def _resp(status, payload):
        captured["status"] = status
        captured["payload"] = payload

    def _raw(status, ctype, payload, extras=None):
        captured["status"] = status
        captured["raw"] = payload
        captured["headers"] = dict(extras or {})

    h._json_response.side_effect = _resp
    h._raw_response.side_effect = _raw
    return h, captured


def _user(username, user_id, *, role_slug="adult", provider_refs=None):
    u = MagicMock()
    u.username = username
    u.id = user_id
    u.role_slug = role_slug
    u.provider_refs = provider_refs or {}
    return u


def _role(admin: bool):
    r = MagicMock()
    r.controller_admin = admin
    return r


class _StubAuthelia:
    """Stub Authelia file provider — records the on-disk disabled
    state so the assertion "users_database.yml reflects disabled:
    true" is observable without running Authelia."""

    name = "authelia"

    def __init__(self) -> None:
        self.disabled: set[str] = set()
        self.deny_rules: set[str] = set()

    def disable_user(self, external_id: str) -> None:
        self.disabled.add(external_id)

    def enable_user(self, external_id: str) -> None:
        self.disabled.discard(external_id)

    def revoke_sessions(self, external_id: str) -> None:
        return None

    def add_ip_deny(self, rule) -> None:
        self.deny_rules.add(rule.cidr)

    def remove_ip_deny(self, cidr: str) -> None:
        self.deny_rules.discard(cidr)


class _StubJellyfin:
    name = "jellyfin"

    def __init__(self) -> None:
        self.disabled: set[str] = set()

    def disable_user(self, external_id: str) -> None:
        self.disabled.add(external_id)

    def enable_user(self, external_id: str) -> None:
        self.disabled.discard(external_id)

    def revoke_sessions(self, external_id: str) -> None:
        return None


class _SessionVisibilityE2E(unittest.TestCase):
    """Harness for one happy-path run through every endpoint."""

    def setUp(self) -> None:
        _user_mgmt_limiter.reset()
        _global_post_limiter.reset()
        IdempotencyCacheRegistry.set_default(None)
        self.authelia = _StubAuthelia()
        self.jellyfin = _StubJellyfin()
        self.session_store = SessionStore(
            default_ttl_seconds=60, idle_ttl_seconds=0,
        )
        self.bus = EventBus()
        self.events: list = []
        self.bus.subscribe_all(self.events.append)
        self.admin = _user(
            "admin", "u-admin", role_slug="admin",
            provider_refs={"authelia": "admin", "jellyfin": "admin-jf"},
        )
        self.alice = _user(
            "alice", "u-alice",
            provider_refs={"authelia": "alice", "jellyfin": "alice-jf"},
        )
        self.svc = MagicMock()
        self.svc._providers = [self.authelia, self.jellyfin]
        self.svc._store = MagicMock()
        self.svc._store.list_all.return_value = [self.admin, self.alice]
        self.svc._store.get_by_username.side_effect = (
            lambda u: {"admin": self.admin, "alice": self.alice}.get(u)
        )
        self.svc._audit = MagicMock()
        self.svc._roles = MagicMock()
        self.svc._roles.get.side_effect = (
            lambda s: {"admin": _role(True), "adult": _role(False)}.get(s)
        )
        self.token_store = MagicMock()
        self.token_store.rotate_signing_secret = MagicMock()
        self.cache = IdempotencyCache(max_entries=32, ttl_seconds=60)
        self.handlers = sph.SecurityPostHandlers(
            ban_store_getter=lambda: _InMemBanStore(),
            session_store=self.session_store,
            token_store_builder=lambda: self.token_store,
            user_service_builder=lambda: self.svc,
            cache=self.cache, event_bus=self.bus,
        )
        self._orig_sph = sph._security_post_handlers
        sph._security_post_handlers = self.handlers
        import media_stack.api.handlers_post as hp
        self._orig_hp = hp._security_post_handlers
        hp._security_post_handlers = self.handlers
        self._actor_patch = patch(
            "media_stack.api.handlers_post._actor_resolver",
        )
        mr = self._actor_patch.start()
        mr.resolve.return_value = Actor(
            username="admin", is_admin=True,
            client_ip="127.0.0.1", user_agent="ua",
        )

    def tearDown(self) -> None:
        self._actor_patch.stop()
        import media_stack.api.handlers_post as hp
        hp._security_post_handlers = self._orig_hp
        sph._security_post_handlers = self._orig_sph

    def test_full_session_visibility_flow(self) -> None:
        srv = PostRequestHandler()
        # 1. Admin creates two sessions (simulating two browser tabs).
        s_one, _ = self.session_store.create(owner_username="admin")
        _s_two, _p_two = self.session_store.create(owner_username="admin")

        # 2. Admin revokes the first session by id.
        before = self.session_store.count()
        h, cap = _handler(
            f"/api/users/u-admin/sessions/{s_one.id}/revoke",
            {"reason": "admin_revoke"},
        )
        srv.handle(h)
        self.assertEqual(cap["status"], 200)
        self.assertEqual(self.session_store.count(), before - 1)

        # 3. Admin bans alice → Authelia + Jellyfin reflect disabled:true.
        h, cap = _handler(
            "/api/bans/users",
            {"username": "alice", "reason": "credential_stuffing"},
            idem="e2e-ban-alice",
        )
        srv.handle(h)
        self.assertEqual(cap["status"], 200)
        self.assertIn("alice", self.authelia.disabled)
        self.assertIn("alice-jf", self.jellyfin.disabled)

        # 4. Admin unbans alice → both providers re-enable.
        h, cap = _handler("/api/bans/users/alice/remove", {})
        srv.handle(h)
        self.assertEqual(cap["status"], 200)
        self.assertNotIn("alice", self.authelia.disabled)
        self.assertNotIn("alice-jf", self.jellyfin.disabled)

        # 5. Admin creates an IP ban → Authelia deny rule added.
        h, cap = _handler(
            "/api/bans/ips",
            {"cidr": "203.0.113.0/24", "reason": "other"},
            idem="e2e-ip-1",
        )
        srv.handle(h)
        self.assertEqual(cap["status"], 200)
        self.assertIn("203.0.113.0/24", self.authelia.deny_rules)

        # 6. Emergency revoke — all remaining sessions gone, event fired.
        h, cap = _handler(
            "/api/emergency-revoke-all",
            {"reason": "e2e drill 2026-04-24"},
            idem="e2e-er",
        )
        srv.handle(h)
        self.assertEqual(cap["status"], 200)
        self.assertTrue(cap["payload"]["secrets_rotated"])
        self.assertEqual(self.session_store.count(), 0)
        self.assertTrue(any(
            getattr(e, "event_type", "") == "security.emergency_revoke"
            for e in self.events
        ))
        self.token_store.rotate_signing_secret.assert_called()

    def test_me_revoke_others_keeps_current(self) -> None:
        srv = PostRequestHandler()
        _s1, p1 = self.session_store.create(owner_username="alice")
        s2, _ = self.session_store.create(owner_username="alice")
        # Switch actor to alice for this call.
        self._actor_patch.stop()
        p = patch(
            "media_stack.api.handlers_post._actor_resolver",
        )
        mr = p.start()
        mr.resolve.return_value = Actor(
            username="alice", is_admin=False,
            client_ip="127.0.0.1", user_agent="ua",
        )
        try:
            h, cap = _handler(
                "/api/me/revoke-others", {},
                cookie=f"ms_session={p1}", idem="me-1",
            )
            srv.handle(h)
            self.assertEqual(cap["status"], 200)
            self.assertIn(s2.id, cap["payload"]["revoked"])
            self.assertIsNotNone(self.session_store.get(p1))
        finally:
            p.stop()
            self._actor_patch = patch(
                "media_stack.api.handlers_post._actor_resolver",
            )
            self._actor_patch.start()


class _InMemBanStore:
    def __init__(self) -> None:
        self._u: dict[str, object] = {}
        self._i: dict[str, object] = {}

    def add_user_ban(self, ban):
        self._u.setdefault(ban.username, ban)
        return self._u[ban.username]

    def remove_user_ban(self, username: str):
        return self._u.pop(username, None)

    def add_ip_ban(self, ban):
        self._i.setdefault(ban.cidr, ban)
        return self._i[ban.cidr]

    def remove_ip_ban(self, cidr: str):
        return self._i.pop(cidr, None)


if __name__ == "__main__":
    unittest.main()
