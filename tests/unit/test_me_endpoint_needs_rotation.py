"""Phase 3: /api/me returns needs_rotation so the dashboard can
gate on a forced-rotation modal while the admin is still sitting
on STACK_ADMIN_PASSWORD.

The dashboard UI is exercised in a browser; these tests pin the
contract between the controller and that UI so a future refactor
can't silently break the gate.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import base64

from media_stack.api.handlers_get import GetRequestHandler  # noqa: E402


def _build_response(user_row: dict | None, username: str) -> dict:
    """Drive GetRequestHandler._build_me_response with a stubbed
    handler + service. Identity resolution has several fallbacks
    (cookie → trusted-proxy → Basic auth); we go straight to Basic
    auth by putting the credentials in the Authorization header."""
    helper = GetRequestHandler.__new__(GetRequestHandler)
    handler = MagicMock()
    auth = "Basic " + base64.b64encode(f"{username}:x".encode()).decode()
    handler.headers = {"Authorization": auth} if username else {}
    svc = MagicMock()
    svc.list_users.return_value = [user_row] if user_row else []
    from media_stack.api.session_singletons import (
        session_cookie_reader, trusted_proxy_auth,
    )
    session_cookie_reader.username_for_handler = lambda h: ""
    trusted_proxy_auth.identity = lambda h: ""
    return helper._build_me_response(handler, svc)


class MeNeedsRotationTests(unittest.TestCase):

    def test_env_seed_admin_needs_rotation(self):
        """A freshly-seeded admin (source=env-seed) must surface
        needs_rotation=true so the dashboard's rotation modal
        engages on first load. Without this the user sails past
        the gate and the env backdoor stays open."""
        resp = _build_response(
            {"id": "u1", "email": "a@x", "username": "admin",
             "display_name": "A", "role_slug": "superadmin",
             "last_login_at": "", "source": "env-seed"},
            username="admin",
        )
        self.assertTrue(resp["authenticated"])
        self.assertTrue(resp["needs_rotation"])
        self.assertEqual(resp["source"], "env-seed")

    def test_env_legacy_admin_needs_rotation(self):
        """Migration path: admin-bootstrap linked an existing Authelia
        admin row with source=env-legacy. Must still trigger the
        rotation modal — the env credential is still valid."""
        resp = _build_response(
            {"id": "u1", "email": "a@x", "username": "admin",
             "display_name": "A", "role_slug": "superadmin",
             "last_login_at": "", "source": "env-legacy"},
            username="admin",
        )
        self.assertTrue(resp["needs_rotation"])

    def test_rotated_admin_does_not_need_rotation(self):
        """Post-rotation state: modal must NOT reappear. Would be
        a recurring nag otherwise and train users to dismiss it."""
        resp = _build_response(
            {"id": "u1", "email": "a@x", "username": "admin",
             "display_name": "A", "role_slug": "superadmin",
             "last_login_at": "", "source": "rotated"},
            username="admin",
        )
        self.assertFalse(resp["needs_rotation"])
        self.assertEqual(resp["source"], "rotated")

    def test_invite_user_does_not_need_rotation(self):
        """Non-admin users created via invite have source=invite and
        must never see the admin-bootstrap rotation modal."""
        resp = _build_response(
            {"id": "u2", "email": "j@x", "username": "jane",
             "display_name": "J", "role_slug": "adult",
             "last_login_at": "", "source": "invite"},
            username="jane",
        )
        self.assertFalse(resp["needs_rotation"])

    def test_user_without_source_field_does_not_need_rotation(self):
        """Forward compatibility: rows created before the source
        field existed have empty string. Must not flag rotation."""
        resp = _build_response(
            {"id": "u1", "email": "a@x", "username": "admin",
             "display_name": "A", "role_slug": "superadmin",
             "last_login_at": ""},
            username="admin",
        )
        self.assertFalse(resp["needs_rotation"])

    def test_anonymous_response_has_no_rotation_field(self):
        """Unauthenticated callers don't trigger any rotation
        machinery — the field should be absent / falsy."""
        resp = _build_response(None, username="")
        self.assertFalse(resp.get("authenticated"))
        self.assertFalse(resp.get("needs_rotation", False))


if __name__ == "__main__":
    unittest.main()
