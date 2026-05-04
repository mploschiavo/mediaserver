"""Integration test: user-mgmt POST endpoints enforce rate limit and CSRF.

ADR-0007 Phase 2 Phase E retired the legacy
``PostRequestHandler.handle()`` chain. The contract is now enforced
by ``PostMutationGate(rate_limit=True)`` on the
``UsersPostRoutes`` route module.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.rate_limiters import _user_mgmt_limiter  # noqa: E402


def _handler(path: str, body: dict, *, client="1.2.3.4", cookie="", csrf=""):
    h = MagicMock()
    h.path = path
    h.client_address = (client, 0)
    h._read_json_body.return_value = body
    headers = {"Cookie": cookie, "X-CSRF-Token": csrf}
    h.headers = MagicMock()
    h.headers.get.side_effect = lambda k, default="": headers.get(k, default)
    captured: dict = {}

    def _respond(status, payload):
        captured["status"] = status
        captured["payload"] = payload
    h._json_response.side_effect = _respond
    return h, captured


def _build_users_routes(fake_service):
    """Build a UsersPostRoutes wired against a fake user service."""
    from media_stack.api.routes.post_users import (
        ActorResolution,
        UserMgmtRepository,
        UsersPostRoutes,
    )

    class _StubRepo(UserMgmtRepository):
        def service(self):
            return fake_service

    class _StubResolver(ActorResolution):
        def resolve(self, handler, body):
            actor = MagicMock()
            actor.username = "admin"
            actor.is_admin = True
            actor.audit_label = "admin"
            return actor

    return UsersPostRoutes(
        repository=_StubRepo(),
        actor_resolution=_StubResolver(),
    )


class RateLimitCsrfTests(unittest.TestCase):
    def setUp(self):
        _user_mgmt_limiter.reset()

    def test_rate_limit_blocks_after_burst(self):
        fake = MagicMock()
        fake.create_user.return_value = {"id": "u1", "email": "a@x"}
        routes = _build_users_routes(fake)
        # With capacity=10, 10 bursts pass and the 11th should fail.
        for _ in range(10):
            h, captured = _handler(
                "/api/users",
                {"email": "a@x", "username": "a",
                 "display_name": "A", "role_slug": "adult"},
            )
            routes.handle_user_create(h)
            self.assertEqual(captured["status"], 200)
        h, captured = _handler(
            "/api/users",
            {"email": "b@x", "username": "b",
             "display_name": "B", "role_slug": "adult"},
        )
        routes.handle_user_create(h)
        self.assertEqual(captured["status"], 429)

    def test_csrf_enforced_when_flag_set(self):
        fake = MagicMock()
        fake.create_user.return_value = {"id": "u1"}
        routes = _build_users_routes(fake)
        with patch.dict("os.environ", {"CSRF_ENFORCE": "1"}, clear=False):
            # Missing CSRF header -> 403
            h, captured = _handler(
                "/api/users",
                {"email": "a@x", "username": "a",
                 "display_name": "A", "role_slug": "adult"},
            )
            routes.handle_user_create(h)
            self.assertEqual(captured["status"], 403)
            # Header present AND matches cookie -> allowed
            h, captured = _handler(
                "/api/users",
                {"email": "a@x", "username": "a",
                 "display_name": "A", "role_slug": "adult"},
                cookie="media_stack_csrf=tok123", csrf="tok123",
            )
            routes.handle_user_create(h)
            self.assertEqual(captured["status"], 200)

    def test_csrf_disabled_by_default(self):
        fake = MagicMock()
        fake.create_user.return_value = {"id": "u1"}
        routes = _build_users_routes(fake)
        with patch.dict("os.environ", {"CSRF_ENFORCE": "0"}, clear=False):
            h, captured = _handler(
                "/api/users",
                {"email": "a@x", "username": "a",
                 "display_name": "A", "role_slug": "adult"},
            )
            routes.handle_user_create(h)
            self.assertEqual(captured["status"], 200)


if __name__ == "__main__":
    unittest.main()
