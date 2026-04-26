"""Tests for ``ActorResolver`` — Actor built from a user's role.

The old dispatcher built ``Actor(is_admin=True)`` unconditionally, so
``@requires_admin`` was a no-op. ``ActorResolver.resolve`` fixes that
by looking up the caller's role via ``UserQueryService`` and reading
``role.controller_admin``. Tests pin:

  * Admin role → ``is_admin=True``, ``role.slug`` in ``actor.roles``.
  * Non-admin role → ``is_admin=False``.
  * Unknown user → bootstrap fallback Actor with ``is_admin=True``
    (preserves env-var admin path on fresh deploys).
  * ``client_ip`` + ``user_agent`` are plumbed onto the Actor.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.actor_resolver import ActorResolver  # noqa: E402


class _FakeHeaders:
    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def get(self, name: str, default: str = "") -> str:
        return self._m.get(name, default)


class _FakeHandler:
    def __init__(self, client_ip: str = "10.0.0.5", headers: dict | None = None):
        self.client_address = (client_ip, 0)
        self.headers = _FakeHeaders(headers or {})


class _Helpers:

    @staticmethod
    def build_service(user: dict | None, role: dict | None) -> MagicMock:
        svc = MagicMock()
        svc.get_user_by_username.return_value = user
        svc.get_role.return_value = role
        return svc

    @staticmethod
    def trusted_ip(_handler) -> str:
        return "198.51.100.42"

    @staticmethod
    def make_resolver(svc, client_ip_for=None) -> ActorResolver:
        return ActorResolver(
            build_service=lambda: svc,
            client_ip_for=client_ip_for or _Helpers.trusted_ip,
        )


class AdminRoleLookupTests(unittest.TestCase):

    def test_admin_role_sets_is_admin_true(self):
        user = {"id": "u1", "username": "alice", "role_slug": "superadmin"}
        role = {"slug": "superadmin", "controller_admin": True}
        svc = _Helpers.build_service(user, role)
        resolver = _Helpers.make_resolver(svc)
        handler = _FakeHandler(headers={"User-Agent": "Mozilla/5.0"})
        actor = resolver.resolve(handler, {"_actor": "alice"})
        self.assertEqual(actor.username, "alice")
        self.assertTrue(actor.is_admin)
        self.assertIn("superadmin", actor.roles)
        self.assertEqual(actor.client_ip, "198.51.100.42")
        self.assertEqual(actor.user_agent, "Mozilla/5.0")
        self.assertEqual(actor.source_provider, "controller")

    def test_non_admin_role_sets_is_admin_false(self):
        """adult role has ``controller_admin: false`` in the shipped
        catalog — the decorator MUST reject admin-only endpoints."""
        user = {"id": "u2", "username": "jane", "role_slug": "adult"}
        role = {"slug": "adult", "controller_admin": False}
        svc = _Helpers.build_service(user, role)
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "jane"})
        self.assertFalse(actor.is_admin)
        self.assertEqual(actor.roles, frozenset({"adult"}))

    def test_family_admin_role_controller_read_only(self):
        """family_admin can manage household but is NOT a controller
        admin — is_admin must be False. Double-checks the resolver
        reads the right flag, not any 'admin-ish' name heuristic."""
        user = {"id": "u3", "username": "fa", "role_slug": "family_admin"}
        role = {"slug": "family_admin", "controller_admin": False}
        svc = _Helpers.build_service(user, role)
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "fa"})
        self.assertFalse(actor.is_admin)

    def test_unknown_user_falls_back_to_bootstrap(self):
        """Fresh deploy / env-var admin: no row in the store. Fallback
        Actor MUST have is_admin=True so STACK_ADMIN_PASSWORD still
        lets the operator bootstrap the stack."""
        svc = _Helpers.build_service(None, None)
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "admin"})
        self.assertEqual(actor.username, "admin")
        self.assertTrue(actor.is_admin)
        self.assertEqual(actor.roles, frozenset())
        svc.get_user_by_username.assert_called_once_with("admin")

    def test_user_exists_but_role_missing_falls_back(self):
        """Role catalog was stripped / misconfigured — we should NOT
        pass is_admin=False silently (that would break every admin
        until someone notices). Fall back to bootstrap semantics."""
        user = {"id": "u4", "username": "op", "role_slug": "ghost"}
        svc = _Helpers.build_service(user, None)
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "op"})
        self.assertTrue(actor.is_admin)

    def test_default_actor_name_when_missing(self):
        """Body without ``_actor`` → resolver synthesises
        ``controller-ui`` so we have something to log."""
        svc = _Helpers.build_service(None, None)
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {})
        self.assertEqual(actor.username, "controller-ui")

    def test_client_ip_and_user_agent_plumbed(self):
        user = {"id": "u5", "username": "u", "role_slug": "adult"}
        role = {"slug": "adult", "controller_admin": False}
        svc = _Helpers.build_service(user, role)
        resolver = _Helpers.make_resolver(
            svc, client_ip_for=lambda _h: "2001:db8::abcd",
        )
        handler = _FakeHandler(headers={"User-Agent": "curl/8.5.0"})
        actor = resolver.resolve(handler, {"_actor": "u"})
        self.assertEqual(actor.client_ip, "2001:db8::abcd")
        self.assertEqual(actor.user_agent, "curl/8.5.0")

    def test_service_build_failure_falls_back(self):
        """UserService factory raises (DB down, YAML unreadable) — the
        resolver must not crash the handler. Fallback Actor is
        returned so the dispatcher can still return 5xx cleanly."""
        def _boom():
            raise RuntimeError("db down")

        resolver = ActorResolver(
            build_service=_boom, client_ip_for=_Helpers.trusted_ip,
        )
        actor = resolver.resolve(_FakeHandler(), {"_actor": "ops"})
        self.assertEqual(actor.username, "ops")
        self.assertTrue(actor.is_admin)

    def test_client_ip_for_failure_returns_empty_ip(self):
        """trusted_proxy_auth.client_ip blows up — Actor is still built,
        just with an empty client_ip (audit rows will be missing the
        client, which a ratchet should catch later)."""
        user = {"id": "u6", "username": "u", "role_slug": "adult"}
        role = {"slug": "adult", "controller_admin": False}
        svc = _Helpers.build_service(user, role)

        def _boom(_h):
            raise RuntimeError("ip lookup failed")

        resolver = _Helpers.make_resolver(svc, client_ip_for=_boom)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "u"})
        self.assertEqual(actor.client_ip, "")
        self.assertFalse(actor.is_admin)

    def test_get_user_by_username_missing_on_service(self):
        """Older UserService without ``get_user_by_username`` — the
        resolver must not AttributeError. Falls back to bootstrap."""
        svc = MagicMock(spec=[])  # no attributes
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "x"})
        self.assertEqual(actor.username, "x")
        self.assertTrue(actor.is_admin)

    def test_empty_role_slug_falls_back(self):
        """User row with blank role_slug (corrupted import) — fall back
        to bootstrap rather than treating as non-admin."""
        user = {"id": "u7", "username": "u", "role_slug": ""}
        svc = _Helpers.build_service(user, None)
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "u"})
        self.assertTrue(actor.is_admin)
        svc.get_role.assert_not_called()

    def test_user_lookup_exception_falls_back(self):
        """``get_user_by_username`` itself raises (DB glitch). Treat
        as missing user and fall back to bootstrap."""
        svc = MagicMock()
        svc.get_user_by_username.side_effect = RuntimeError("db read")
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "op"})
        self.assertTrue(actor.is_admin)

    def test_role_lookup_exception_falls_back(self):
        """``get_role`` raises — role catalog corruption. Fall back
        rather than running with an undefined role."""
        user = {"id": "u8", "username": "op", "role_slug": "adult"}
        svc = MagicMock()
        svc.get_user_by_username.return_value = user
        svc.get_role.side_effect = RuntimeError("role catalog parse")
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_FakeHandler(), {"_actor": "op"})
        self.assertTrue(actor.is_admin)

    def test_handler_without_headers_does_not_crash(self):
        """Odd handler (e.g. unit-test stubs) without a ``headers``
        attribute — ``user_agent`` defaults to ''."""

        class _StubHandler:
            client_address = ("10.0.0.5", 0)
            headers = None

        svc = _Helpers.build_service(None, None)
        resolver = _Helpers.make_resolver(svc)
        actor = resolver.resolve(_StubHandler(), {"_actor": "x"})
        self.assertEqual(actor.user_agent, "")


class ResolveActorIntegrationTests(unittest.TestCase):
    """Integration through the real UserQueryService surface: exercises
    ``get_user_by_username`` + ``get_role`` that we added."""

    def test_user_service_query_methods_exist(self):
        from media_stack.core.auth.users.user_service import UserQueryService
        # Keep the surface pinned: if either method disappears,
        # resolve_actor falls back silently and authz quietly opens up.
        self.assertTrue(hasattr(UserQueryService, "get_user_by_username"))
        self.assertTrue(hasattr(UserQueryService, "get_role"))

    def test_get_user_by_username_returns_none_for_blank(self):
        from media_stack.core.auth.users.user_service import UserQueryService

        class _NullStore:
            def get_by_username(self, _u):
                return None

        svc = UserQueryService.__new__(UserQueryService)
        svc._store = _NullStore()
        self.assertIsNone(svc.get_user_by_username(""))
        self.assertIsNone(svc.get_user_by_username("nobody"))


if __name__ == "__main__":
    unittest.main()
