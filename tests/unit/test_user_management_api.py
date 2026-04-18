"""Tests for user-management HTTP endpoints on the controller API."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _post_handler(path: str, body: dict):
    h = MagicMock()
    h.path = path
    h._read_json_body.return_value = body
    # Default: no cookie → API-client path, CSRF smart-default lets it through.
    h.headers.get.side_effect = lambda name, default="": default
    h.client_address = ("127.0.0.1", 0)
    captured: dict = {}

    def _respond(status, payload):
        captured["status"] = status
        captured["payload"] = payload
    h._json_response.side_effect = _respond
    return h, captured


def _get_handler(path: str):
    h = MagicMock()
    h.path = path
    captured: dict = {}

    def _respond(status, payload):
        captured["status"] = status
        captured["payload"] = payload
    h._json_response.side_effect = _respond
    return h, captured


class UserManagementApiTests(unittest.TestCase):

    def test_post_users_create(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = _post_handler("/api/users", {
            "email": "jane@x", "username": "jane", "display_name": "Jane",
            "role_slug": "adult",
        })
        fake_service = MagicMock()
        fake_service.create_user.return_value = {"id": "u1", "email": "jane@x",
                                                 "generated_password": "p"}
        with patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake_service,
        ):
            svc.handle(h)
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["email"], "jane@x")
        fake_service.create_user.assert_called_once()

    def test_post_users_role_change(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = _post_handler("/api/users/u1/role", {"role_slug": "kid"})
        fake_service = MagicMock()
        fake_service.set_role.return_value = {"user_id": "u1", "role_slug": "kid"}
        with patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake_service,
        ):
            svc.handle(h)
        self.assertEqual(captured["status"], 200)
        fake_service.set_role.assert_called_once()

    def test_post_users_reset_password(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = _post_handler("/api/users/u1/reset-password", {})
        fake_service = MagicMock()
        fake_service.reset_password.return_value = {"generated_password": "newp"}
        with patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake_service,
        ):
            svc.handle(h)
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["generated_password"], "newp")

    def test_post_users_delete_via_action_path(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = _post_handler("/api/users/u1/delete", {})
        fake_service = MagicMock()
        fake_service.delete_user.return_value = {"user_id": "u1", "providers": {}}
        with patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake_service,
        ):
            svc.handle(h)
        self.assertEqual(captured["status"], 200)
        fake_service.delete_user.assert_called_once_with("u1", actor="controller-ui")

    def test_post_users_unknown_action_400(self):
        from media_stack.api.handlers_post import PostRequestHandler
        svc = PostRequestHandler()
        h, captured = _post_handler("/api/users/u1/unknown-thing", {})
        svc.handle(h)
        self.assertEqual(captured["status"], 400)

    def test_post_users_service_error_returns_400(self):
        from media_stack.api.handlers_post import PostRequestHandler
        from media_stack.core.auth.users.user_service import UserServiceError
        svc = PostRequestHandler()
        h, captured = _post_handler("/api/users", {"email": "", "username": "",
                                                    "display_name": "", "role_slug": "adult"})
        fake_service = MagicMock()
        fake_service.create_user.side_effect = UserServiceError("bad input")
        with patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake_service,
        ):
            svc.handle(h)
        self.assertEqual(captured["status"], 400)
        self.assertIn("bad input", captured["payload"]["error"])


if __name__ == "__main__":
    unittest.main()
