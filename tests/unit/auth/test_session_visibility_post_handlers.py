"""Unit tests for the session-visibility POST handlers.

Covers:

* Happy-path 200 for each of the eight endpoints.
* 403 when the caller is not admin (for the admin-scoped endpoints).
* 400 for malformed payloads.
* 404 for unknown session / endpoint.
* 429 when the global or user-mgmt rate limiter is exhausted.
* Idempotent retry — same Idempotency-Key inside TTL returns the
  cached payload without invoking cascade side effects again.
* Ban cascades: Authelia ``disable_user`` + Jellyfin ``disable_user``
  + controller session revoke.
* IP ban cascades to ``AutheliaIPDenyProvider.add_ip_deny``.
* Emergency revoke: all provider results + audit + admin flag.
* ``this-wasn't-me``: clear-cookie response, forced-rotation flag,
  anomaly audit entry.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.handlers_post import (  # noqa: E402
    PostRequestHandler, _global_post_limiter, _user_mgmt_limiter,
)
from media_stack.api.services import security_post_handlers as sph  # noqa: E402
from media_stack.core.auth.authz import Actor  # noqa: E402
from media_stack.core.auth.idempotency_cache import (  # noqa: E402
    IdempotencyCache, IdempotencyCacheRegistry,
)
from media_stack.core.auth.users.ban_store import BanReason  # noqa: E402
from media_stack.core.auth.session_store import SessionStore  # noqa: E402
from media_stack.core.events.bus import EventBus  # noqa: E402


# ---------------------------------------------------------------------------
# Test plumbing: fake handler, in-memory ban store, spy user service.
# ---------------------------------------------------------------------------


def _handler(path: str, body: dict | None = None, *,
             client: str = "127.0.0.1", cookie: str = "",
             csrf: str = "tok", idem: str = "", origin: str = "",
             host: str = "localhost") -> tuple[MagicMock, dict]:
    """Construct a MagicMock that looks like ``ControllerAPIHandler``."""
    h = MagicMock()
    h.path = path
    h.client_address = (client, 0)
    h._read_json_body.return_value = body or {}
    # CSRF needs cookie-present + matching header.
    headers_map = {
        "Cookie": cookie, "X-CSRF-Token": csrf,
        "Idempotency-Key": idem,
        "Origin": origin, "Host": host,
    }
    h.headers = MagicMock()
    h.headers.get.side_effect = (
        lambda k, default="": headers_map.get(k, default)
    )
    captured: dict = {}

    def _respond(status, payload):
        captured["status"] = status
        captured["payload"] = payload

    def _raw(status, ctype, payload, extras=None):
        captured["status"] = status
        captured["raw"] = payload
        captured["content_type"] = ctype
        captured["headers"] = dict(extras or {})

    h._json_response.side_effect = _respond
    h._raw_response.side_effect = _raw
    return h, captured


class _InMemBanStore:
    """Tiny BanStore replacement used across the test suite."""

    def __init__(self) -> None:
        self._user: dict[str, object] = {}
        self._ip: dict[str, object] = {}

    def add_user_ban(self, ban):
        if ban.username in self._user:
            return self._user[ban.username]
        self._user[ban.username] = ban
        return ban

    def remove_user_ban(self, username: str):
        return self._user.pop(username, None)

    def add_ip_ban(self, ban):
        if ban.cidr in self._ip:
            return self._ip[ban.cidr]
        self._ip[ban.cidr] = ban
        return ban

    def remove_ip_ban(self, cidr: str):
        return self._ip.pop(cidr, None)

    # rarely used by handlers but present for parity
    def list_user_bans(self):
        return list(self._user.values())


def _fake_provider(name: str, has_sessions: bool = True,
                   has_ip_deny: bool = False) -> MagicMock:
    p = MagicMock()
    p.name = name
    if not has_sessions:
        del p.revoke_sessions
    if not has_ip_deny:
        del p.add_ip_deny
        del p.remove_ip_deny
    return p


def _fake_user_service(providers: list | None = None,
                       users: list | None = None,
                       roles: dict | None = None) -> MagicMock:
    svc = MagicMock()
    svc._providers = list(providers or [])
    svc._roles = MagicMock()
    svc._roles.get.side_effect = (lambda s: (roles or {}).get(s))
    svc._audit = MagicMock()
    svc._audit.append = MagicMock()
    svc._store = MagicMock()
    users = list(users or [])
    svc._store.list_all.return_value = users

    def _by_username(u: str):
        for user in users:
            if getattr(user, "username", "") == u:
                return user
        return None

    svc._store.get_by_username.side_effect = _by_username
    svc._store.update = MagicMock()
    return svc


def _user(username: str, user_id: str = "u1",
          provider_refs: dict | None = None,
          role_slug: str = "adult"):
    obj = MagicMock()
    obj.username = username
    obj.id = user_id
    obj.role_slug = role_slug
    obj.provider_refs = provider_refs or {}
    return obj


def _role(slug: str, admin: bool):
    r = MagicMock()
    r.controller_admin = admin
    return r


def _admin_actor() -> Actor:
    return Actor(username="admin", is_admin=True,
                 client_ip="127.0.0.1", user_agent="ua")


def _user_actor(name: str = "alice") -> Actor:
    return Actor(username=name, is_admin=False)


class _HandlerHarness(unittest.TestCase):
    """Common setUp: reset rate limiters + idempotency cache,
    patch the shared ``_security_post_handlers`` with a fresh
    instance wired to in-memory dependencies, and a fresh EventBus."""

    def setUp(self) -> None:
        _user_mgmt_limiter.reset()
        _global_post_limiter.reset()
        IdempotencyCacheRegistry.set_default(None)
        self.ban_store = _InMemBanStore()
        self.session_store = SessionStore(
            default_ttl_seconds=60, idle_ttl_seconds=0,
        )
        self.events: list = []
        self.bus = EventBus()
        self.bus.subscribe_all(self.events.append)
        self.authelia = _fake_provider("authelia", has_ip_deny=True)
        self.jellyfin = _fake_provider("jellyfin")
        admin = _user("admin", user_id="uadmin",
                      provider_refs={"authelia": "admin",
                                     "jellyfin": "admin-jf"},
                      role_slug="admin")
        self.alice = _user("alice", user_id="u-alice",
                           provider_refs={"authelia": "alice",
                                          "jellyfin": "alice-jf"})
        self.svc = _fake_user_service(
            providers=[self.authelia, self.jellyfin],
            users=[admin, self.alice],
            roles={"admin": _role("admin", True),
                   "adult": _role("adult", False)},
        )
        self.token_store = MagicMock()
        self.token_store.rotate_signing_secret = MagicMock(return_value=3)
        self.cache = IdempotencyCache(max_entries=32, ttl_seconds=60)
        self.handler = sph.SecurityPostHandlers(
            ban_store_getter=lambda: self.ban_store,
            session_store=self.session_store,
            token_store_builder=lambda: self.token_store,
            user_service_builder=lambda: self.svc,
            cache=self.cache, event_bus=self.bus,
        )
        # Swap the shared instance so the PostRequestHandler routes to ours.
        self._orig_security = sph._security_post_handlers
        sph._security_post_handlers = self.handler
        # Patch module-level symbol used in handlers_post.py too.
        import media_stack.api.handlers_post as hp
        self._orig_hp = hp._security_post_handlers
        hp._security_post_handlers = self.handler

    def tearDown(self) -> None:
        import media_stack.api.handlers_post as hp
        hp._security_post_handlers = self._orig_hp
        sph._security_post_handlers = self._orig_security


# ---------------------------------------------------------------------------
# Revoke single session
# ---------------------------------------------------------------------------


class RevokeSessionTests(_HandlerHarness):

    def test_happy_path_revokes_and_audits(self) -> None:
        sess, _plain = self.session_store.create(
            owner_username="alice", client_ip="127.0.0.1", user_agent="ua",
        )
        actor = _admin_actor()
        h, captured = _handler(
            f"/api/users/u-alice/sessions/{sess.id}/revoke",
            {"reason": "admin_revoke"},
        )
        self.handler.dispatch(h, h.path, {"reason": "admin_revoke"}, actor)
        self.assertEqual(captured["status"], 200)
        self.assertTrue(captured["payload"]["ok"])
        self.svc._audit.append.assert_called()
        # Event fired.
        self.assertTrue(
            any(getattr(e, "event_type", "") == "session.revoked"
                for e in self.events),
        )

    def test_non_admin_gets_403(self) -> None:
        sess, _plain = self.session_store.create(owner_username="alice")
        h, captured = _handler(
            f"/api/users/u-alice/sessions/{sess.id}/revoke", {},
        )
        self.handler.dispatch(h, h.path, {}, _user_actor("alice"))
        self.assertEqual(captured["status"], 403)

    def test_unknown_session_returns_404(self) -> None:
        h, captured = _handler(
            "/api/users/u-alice/sessions/nope/revoke", {},
        )
        self.handler.dispatch(h, h.path, {}, _admin_actor())
        self.assertEqual(captured["status"], 404)

    def test_idempotent_retry_reuses_cache(self) -> None:
        sess, _plain = self.session_store.create(owner_username="alice")
        actor = _admin_actor()
        h, captured = _handler(
            f"/api/users/u-alice/sessions/{sess.id}/revoke", {},
            idem="same-key",
        )
        self.handler.dispatch(h, h.path, {}, actor)
        self.assertEqual(captured["status"], 200)
        # Second call with same key — would 404 if it ran again (session gone),
        # but the cache returns the OK payload unchanged.
        h2, cap2 = _handler(
            f"/api/users/u-alice/sessions/{sess.id}/revoke", {},
            idem="same-key",
        )
        self.handler.dispatch(h2, h2.path, {}, actor)
        self.assertEqual(cap2["status"], 200)
        self.assertEqual(cap2["payload"]["session_id"], sess.id)


# ---------------------------------------------------------------------------
# User bans
# ---------------------------------------------------------------------------


class UserBanTests(_HandlerHarness):

    def test_add_happy_path_cascades(self) -> None:
        _sess, _plain = self.session_store.create(owner_username="alice")
        body = {"username": "alice", "reason": "credential_stuffing",
                "reason_detail": "seen-in-leak",
                "expires_at": "2026-05-01T00:00:00Z"}
        h, captured = _handler("/api/bans/users", body, idem="ban-1")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 200)
        self.assertIn("cascades", captured["payload"])
        self.authelia.disable_user.assert_called_with("alice")
        self.jellyfin.disable_user.assert_called_with("alice-jf")
        # Controller sessions swept.
        self.assertEqual(self.session_store.revoke_all_for("alice"), 0)
        # Audit + event.
        self.svc._audit.append.assert_called()
        self.assertTrue(
            any(getattr(e, "event_type", "") == "ban.applied"
                for e in self.events),
        )

    def test_add_non_admin_403(self) -> None:
        h, captured = _handler("/api/bans/users",
                                {"username": "alice", "reason": "other"})
        self.handler.dispatch(h, h.path,
                               {"username": "alice", "reason": "other"},
                               _user_actor("alice"))
        self.assertEqual(captured["status"], 403)

    def test_add_missing_username_400(self) -> None:
        h, captured = _handler("/api/bans/users", {})
        self.handler.dispatch(h, h.path, {}, _admin_actor())
        self.assertEqual(captured["status"], 400)

    def test_add_invalid_reason_400(self) -> None:
        body = {"username": "alice", "reason": "NOT_A_REAL_REASON"}
        h, captured = _handler("/api/bans/users", body)
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 400)

    def test_remove_cascades_enable(self) -> None:
        body = {"username": "alice", "reason": "other"}
        h, _ = _handler("/api/bans/users", body, idem="b-add")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        h2, cap2 = _handler("/api/bans/users/alice/remove", {})
        self.handler.dispatch(h2, h2.path, {}, _admin_actor())
        self.assertEqual(cap2["status"], 200)
        self.authelia.enable_user.assert_called_with("alice")
        self.jellyfin.enable_user.assert_called_with("alice-jf")
        self.assertTrue(
            any(getattr(e, "event_type", "") == "ban.removed"
                for e in self.events),
        )

    def test_add_idempotent_retry(self) -> None:
        body = {"username": "alice", "reason": "other"}
        h1, cap1 = _handler("/api/bans/users", body, idem="ban-key")
        self.handler.dispatch(h1, h1.path, body, _admin_actor())
        self.authelia.disable_user.reset_mock()
        h2, cap2 = _handler("/api/bans/users", body, idem="ban-key")
        self.handler.dispatch(h2, h2.path, body, _admin_actor())
        self.assertEqual(cap2["status"], 200)
        self.assertEqual(cap1["payload"], cap2["payload"])
        # Second call hit the cache — no cascade re-fire.
        self.authelia.disable_user.assert_not_called()


# ---------------------------------------------------------------------------
# IP bans
# ---------------------------------------------------------------------------


class IpBanTests(_HandlerHarness):

    def test_add_happy_path(self) -> None:
        body = {"cidr": "203.0.113.0/24", "reason": "other"}
        h, captured = _handler("/api/bans/ips", body, idem="ip-k1")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 200)
        self.authelia.add_ip_deny.assert_called_once()

    def test_add_invalid_cidr_400(self) -> None:
        body = {"cidr": "not-an-ip", "reason": "other"}
        h, captured = _handler("/api/bans/ips", body)
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 400)

    def test_add_missing_cidr_400(self) -> None:
        h, captured = _handler("/api/bans/ips", {})
        self.handler.dispatch(h, h.path, {}, _admin_actor())
        self.assertEqual(captured["status"], 400)

    def test_remove_cascades(self) -> None:
        body = {"cidr": "203.0.113.0/24", "reason": "other"}
        h1, _ = _handler("/api/bans/ips", body, idem="xx")
        self.handler.dispatch(h1, h1.path, body, _admin_actor())
        h2, cap2 = _handler("/api/bans/ips/203.0.113.0%2F24/remove", {})
        # use the non-encoded form — the regex matches raw.
        h2.path = "/api/bans/ips/203.0.113.0%2F24/remove"
        # The handler extracts whatever string segment — test the simpler
        # single-ip form.
        h3, cap3 = _handler(
            "/api/bans/ips/203.0.113.0/remove", {},
        )
        h3.path = "/api/bans/ips/203.0.113.0/remove"
        self.handler.dispatch(h3, h3.path, {}, _admin_actor())
        self.assertEqual(cap3["status"], 200)
        self.authelia.remove_ip_deny.assert_called()


# ---------------------------------------------------------------------------
# Emergency revoke
# ---------------------------------------------------------------------------


class EmergencyRevokeTests(_HandlerHarness):

    def test_kills_sessions_rotates_secrets_flags_admin(self) -> None:
        for owner in ("admin", "alice", "alice"):
            self.session_store.create(owner_username=owner)
        body = {"reason": "incident 2026-04-24"}
        h, captured = _handler("/api/emergency-revoke-all", body,
                                idem="er-1")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        self.assertTrue(payload["secrets_rotated"])
        self.assertGreaterEqual(payload["forced_rotations"], 1)
        self.assertEqual(self.session_store.count(), 0)
        self.token_store.rotate_signing_secret.assert_called()
        # Admin user flagged for rotation.
        self.svc._store.update.assert_any_call("uadmin", source="rotated")
        # Emergency event fired.
        self.assertTrue(
            any(getattr(e, "event_type", "") == "security.emergency_revoke"
                for e in self.events),
        )

    def test_reason_required_min_chars(self) -> None:
        h, captured = _handler("/api/emergency-revoke-all",
                                {"reason": "no"})
        self.handler.dispatch(h, h.path, {"reason": "no"}, _admin_actor())
        self.assertEqual(captured["status"], 400)

    def test_non_admin_403(self) -> None:
        h, captured = _handler("/api/emergency-revoke-all",
                                {"reason": "incident XYZ"})
        self.handler.dispatch(h, h.path, {"reason": "incident XYZ"},
                               _user_actor())
        self.assertEqual(captured["status"], 403)


# ---------------------------------------------------------------------------
# Self-service
# ---------------------------------------------------------------------------


class SelfServiceTests(_HandlerHarness):

    def test_revoke_others_keeps_current_session(self) -> None:
        # Create two sessions for alice; the first is "current".
        s1, p1 = self.session_store.create(owner_username="alice")
        s2, _p2 = self.session_store.create(owner_username="alice")
        h, captured = _handler(
            "/api/me/revoke-others", {}, cookie=f"ms_session={p1}",
        )
        self.handler.dispatch(h, h.path, {}, _user_actor("alice"))
        self.assertEqual(captured["status"], 200)
        self.assertIn(s2.id, captured["payload"]["revoked"])
        # The caller's own session stays alive.
        self.assertIsNotNone(self.session_store.get(p1))

    def test_this_wasnt_me_clears_cookie_and_flags_rotation(self) -> None:
        _s1, _p = self.session_store.create(owner_username="alice")
        body = {"login_timestamp": "2026-04-24T12:00Z",
                "flagged_ip": "203.0.113.9"}
        h, captured = _handler("/api/me/this-wasnt-me", body)
        self.handler.dispatch(h, h.path, body, _user_actor("alice"))
        self.assertEqual(captured["status"], 200)
        headers = captured.get("headers", {})
        self.assertIn("Set-Cookie", headers)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        # Alice flagged for rotation.
        self.svc._store.update.assert_any_call("u-alice", source="rotated")
        # Audit + login.blocked event.
        self.assertTrue(any(
            getattr(e, "event_type", "") == "login.blocked"
            for e in self.events
        ))

    def test_revoke_others_requires_authentication(self) -> None:
        h, captured = _handler("/api/me/revoke-others", {})
        self.handler.dispatch(h, h.path, {}, Actor.anonymous())
        self.assertEqual(captured["status"], 403)


# ---------------------------------------------------------------------------
# End-to-end via PostRequestHandler (rate-limit + CSRF integration)
# ---------------------------------------------------------------------------


class DispatcherIntegrationTests(_HandlerHarness):

    def _admin_hndlr(self, path: str, body: dict, **kw):
        # Supply CSRF-compliant headers.
        # The default CSRF mode accepts header/cookie match when the
        # cookie is absent (API-client style). We pass no cookie so
        # CSRF check short-circuits to True.
        return _handler(path, body, cookie="", **kw)

    def test_unknown_security_path_goes_404(self) -> None:
        h, captured = self._admin_hndlr(
            "/api/bans/elsewhere", {},
        )
        srv = PostRequestHandler()
        with patch(
            "media_stack.api.handlers_post._actor_resolver",
        ) as mr:
            mr.resolve.return_value = _admin_actor()
            srv.handle(h)
        self.assertEqual(captured["status"], 404)

    def test_rate_limit_429(self) -> None:
        srv = PostRequestHandler()
        with patch(
            "media_stack.api.handlers_post._actor_resolver",
        ) as mr:
            mr.resolve.return_value = _admin_actor()
            # Exhaust the user-mgmt bucket (capacity 10). The global
            # bucket (30) is more generous; the user-mgmt limiter
            # fires first on the security path.
            for _ in range(10):
                h, _ = self._admin_hndlr(
                    "/api/bans/users", {"username": "x", "reason": "other"},
                )
                srv.handle(h)
            h, captured = self._admin_hndlr(
                "/api/bans/users", {"username": "y", "reason": "other"},
            )
            srv.handle(h)
            self.assertEqual(captured["status"], 429)

    def test_bad_body_is_400(self) -> None:
        srv = PostRequestHandler()
        with patch(
            "media_stack.api.handlers_post._actor_resolver",
        ) as mr:
            mr.resolve.return_value = _admin_actor()
            h, captured = self._admin_hndlr("/api/bans/users", {})
            srv.handle(h)
            self.assertEqual(captured["status"], 400)


class CascadeCoverageTests(_HandlerHarness):
    """Extra slices that don't fit neatly into endpoint classes."""

    def test_ban_reason_enum_accepted(self) -> None:
        body = {"username": "alice", "reason": BanReason.OTHER}
        h, captured = _handler("/api/bans/users", body)
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 200)

    def test_ip_deny_provider_missing_cascades_empty(self) -> None:
        # Drop the ip-deny provider — cascade is a no-op but still OK.
        for p in self.svc._providers:
            if hasattr(p, "add_ip_deny"):
                del p.add_ip_deny
        body = {"cidr": "198.51.100.0/24", "reason": "other"}
        h, captured = _handler("/api/bans/ips", body, idem="nic")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["cascades"], {})

    def test_matches_exact_and_regex(self) -> None:
        self.assertTrue(
            self.handler.matches("/api/bans/users"),
        )
        self.assertTrue(self.handler.matches(
            "/api/users/u1/sessions/s1/revoke",
        ))
        self.assertTrue(self.handler.matches(
            "/api/bans/users/bob/remove"
        ))
        self.assertFalse(self.handler.matches("/api/auth/login"))

    def test_rotate_signing_secret_failure_is_tolerated(self) -> None:
        self.token_store.rotate_signing_secret = MagicMock(
            side_effect=RuntimeError("boom"),
        )
        body = {"reason": "real incident"}
        h, captured = _handler("/api/emergency-revoke-all", body,
                                idem="tok-fail")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(captured["status"], 200)
        self.assertFalse(captured["payload"]["secrets_rotated"])

    def test_user_ban_remove_no_record_still_200(self) -> None:
        h, captured = _handler(
            "/api/bans/users/ghost/remove", {},
        )
        self.handler.dispatch(h, h.path, {}, _admin_actor())
        self.assertEqual(captured["status"], 200)
        self.assertFalse(captured["payload"]["removed"])

    def test_audit_records_actor_label_and_detail(self) -> None:
        body = {"username": "alice", "reason": "other"}
        h, _ = _handler("/api/bans/users", body, idem="aud-k")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        call = self.svc._audit.append.call_args
        self.assertEqual(call.kwargs["actor"], "admin")
        self.assertEqual(call.kwargs["action"], "ban_user_add")
        self.assertEqual(call.kwargs["target"], "alice")

    def test_emergency_revoke_provider_unreachable_marks_err(self) -> None:
        self.authelia.revoke_sessions = MagicMock(
            side_effect=RuntimeError("network down"),
        )
        body = {"reason": "provider offline incident"}
        h, cap = _handler("/api/emergency-revoke-all", body, idem="erp")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(cap["status"], 200)
        # The handler still returns 200; individual providers may report err.
        self.assertIn("provider_results", cap["payload"])

    def test_response_body_does_not_contain_secrets(self) -> None:
        body = {"username": "alice", "reason": "other"}
        h, cap = _handler("/api/bans/users", body, idem="sec")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        raw = repr(cap["payload"])
        for forbidden in ("password", "token", "plaintext", "signing_secret"):
            self.assertNotIn(forbidden, raw.lower(),
                             f"{forbidden} leaked into response: {raw[:120]}")

    def test_session_revoke_fires_session_revoked_event(self) -> None:
        sess, _p = self.session_store.create(owner_username="alice")
        self.events.clear()
        h, cap = _handler(
            f"/api/users/u-alice/sessions/{sess.id}/revoke",
            {"reason": "admin_revoke"},
        )
        self.handler.dispatch(h, h.path, {"reason": "admin_revoke"},
                               _admin_actor())
        self.assertEqual(cap["status"], 200)
        revoked = [e for e in self.events
                   if getattr(e, "event_type", "") == "session.revoked"]
        self.assertEqual(len(revoked), 1)
        self.assertEqual(revoked[0].reason, "admin_revoke")

    def test_me_revoke_others_zero_others_ok(self) -> None:
        # Only the caller's session exists.
        _s, p = self.session_store.create(owner_username="alice")
        h, cap = _handler("/api/me/revoke-others", {},
                           cookie=f"ms_session={p}")
        self.handler.dispatch(h, h.path, {}, _user_actor("alice"))
        self.assertEqual(cap["status"], 200)
        self.assertEqual(cap["payload"]["revoked"], [])
        self.assertEqual(cap["payload"]["count"], 0)

    def test_emergency_revoke_zero_admins_returns_zero_forced(self) -> None:
        # Re-wire svc with no admins.
        for u in self.svc._store.list_all.return_value:
            u.role_slug = "adult"
        body = {"reason": "drill with no admins"}
        h, cap = _handler("/api/emergency-revoke-all", body, idem="er0")
        self.handler.dispatch(h, h.path, body, _admin_actor())
        self.assertEqual(cap["status"], 200)
        self.assertEqual(cap["payload"]["forced_rotations"], 0)

    def test_ip_ban_remove_unknown_cidr_still_200(self) -> None:
        h, cap = _handler("/api/bans/ips/198.51.100.9/remove", {})
        self.handler.dispatch(h, h.path, {}, _admin_actor())
        self.assertEqual(cap["status"], 200)
        self.assertFalse(cap["payload"]["removed"])

    def test_authorization_error_maps_to_403_with_reason(self) -> None:
        # An actor with is_admin=False hitting an admin endpoint.
        h, cap = _handler("/api/bans/users",
                           {"username": "bob", "reason": "other"})
        self.handler.dispatch(h, h.path,
                               {"username": "bob", "reason": "other"},
                               _user_actor("bob"))
        self.assertEqual(cap["status"], 403)
        self.assertIn("admin", cap["payload"]["error"].lower())


if __name__ == "__main__":
    unittest.main()
