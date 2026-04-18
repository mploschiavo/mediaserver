"""Integration test: user-mgmt POST endpoints enforce rate limit and CSRF."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.handlers_post import PostRequestHandler, _user_mgmt_limiter  # noqa: E402


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


class RateLimitCsrfTests(unittest.TestCase):
    def setUp(self):
        _user_mgmt_limiter.reset()

    def test_rate_limit_blocks_after_burst(self):
        svc = PostRequestHandler()
        fake = MagicMock()
        fake.create_user.return_value = {"id": "u1", "email": "a@x"}
        # With capacity=10, 10 bursts pass and the 11th should fail.
        with patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake,
        ):
            for _ in range(10):
                h, captured = _handler("/api/users", {"email": "a@x",
                                                      "username": "a",
                                                      "display_name": "A",
                                                      "role_slug": "adult"})
                svc.handle(h)
                self.assertEqual(captured["status"], 200)
            h, captured = _handler("/api/users", {"email": "b@x",
                                                  "username": "b",
                                                  "display_name": "B",
                                                  "role_slug": "adult"})
            svc.handle(h)
            self.assertEqual(captured["status"], 429)

    def test_csrf_enforced_when_flag_set(self):
        svc = PostRequestHandler()
        fake = MagicMock()
        fake.create_user.return_value = {"id": "u1"}
        with patch("media_stack.api.handlers_post._CSRF_ENFORCE", True), patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake,
        ):
            # Missing CSRF header → 403
            h, captured = _handler("/api/users", {"email": "a@x", "username": "a",
                                                  "display_name": "A",
                                                  "role_slug": "adult"})
            svc.handle(h)
            self.assertEqual(captured["status"], 403)
            # Header present AND matches cookie → allowed
            h, captured = _handler(
                "/api/users",
                {"email": "a@x", "username": "a", "display_name": "A",
                 "role_slug": "adult"},
                cookie="media_stack_csrf=tok123", csrf="tok123",
            )
            svc.handle(h)
            self.assertEqual(captured["status"], 200)

    def test_csrf_disabled_by_default(self):
        svc = PostRequestHandler()
        fake = MagicMock()
        fake.create_user.return_value = {"id": "u1"}
        with patch("media_stack.api.handlers_post._CSRF_ENFORCE", False), patch(
            "media_stack.api.handlers_post.build_default_service",
            return_value=fake,
        ):
            h, captured = _handler("/api/users", {"email": "a@x", "username": "a",
                                                  "display_name": "A",
                                                  "role_slug": "adult"})
            svc.handle(h)
            self.assertEqual(captured["status"], 200)


if __name__ == "__main__":
    unittest.main()
