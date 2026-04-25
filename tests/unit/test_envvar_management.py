"""Tests for the env-var management CRUD surface — focuses on the
DELETE half (``POST /api/envvars/delete``), the partner of the
existing ``POST /api/envvars`` set handler.

What this file pins:

- ``DiagnosticsService.delete_envvar`` removes the key from
  ``os.environ`` and reports ``existed: true`` / ``existed: false``
  so the dashboard can render an idempotent confirmation rather
  than an error.
- The controller's POST dispatcher routes ``/api/envvars/delete``
  through ``_diagnostics.delete_envvar`` and rejects (a) missing
  ``key`` (400) and (b) keys outside the allowed prefix set (400).
- The CSRF gate fires when ``_CSRF_ENFORCE`` is set: a request
  without an ``X-CSRF-Token`` header that matches the
  ``media_stack_csrf`` cookie is rejected with 403 before the
  handler runs.

These tests intentionally do not exercise the HTTP transport — the
``handlers_post.PostRequestHandler.handle`` dispatcher is invoked
with a ``MagicMock`` handler so we can inspect the response shape.
The same helper pattern that ``test_user_mgmt_rate_limit_csrf.py``
uses for /api/users.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.handlers_post import PostRequestHandler  # noqa: E402
from media_stack.api.services.config._diagnostics import (  # noqa: E402
    DiagnosticsService,
)


def _handler(path: str, body: dict, *, client="1.2.3.4", cookie="", csrf=""):
    """Build a fake handler that records ``_json_response`` calls."""
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


class DeleteEnvVarServiceTests(unittest.TestCase):
    """Direct unit tests for ``DiagnosticsService.delete_envvar``."""

    def setUp(self) -> None:
        self._orig_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

    def _svc(self) -> DiagnosticsService:
        return DiagnosticsService(profile=mock.MagicMock())

    def test_delete_existing_key_reports_existed_true(self) -> None:
        os.environ["BOOTSTRAP_TEST_DELETE_KEY"] = "value"
        result = self._svc().delete_envvar("BOOTSTRAP_TEST_DELETE_KEY")
        self.assertEqual(result["status"], "deleted")
        self.assertEqual(result["key"], "BOOTSTRAP_TEST_DELETE_KEY")
        self.assertTrue(result["existed"])
        self.assertNotIn("BOOTSTRAP_TEST_DELETE_KEY", os.environ)

    def test_delete_missing_key_is_idempotent(self) -> None:
        os.environ.pop("BOOTSTRAP_NEVER_SET", None)
        result = self._svc().delete_envvar("BOOTSTRAP_NEVER_SET")
        self.assertEqual(result["status"], "deleted")
        self.assertFalse(result["existed"])


class DeleteEnvVarRouteTests(unittest.TestCase):
    """``POST /api/envvars/delete`` dispatcher — happy path + 400 cases."""

    def setUp(self) -> None:
        self._orig_env = dict(os.environ)
        # Disable CSRF + global rate-limit for the dispatcher tests so
        # we can exercise the route shape without setting up cookies.
        self._csrf_patch = patch(
            "media_stack.api.handlers_post._CSRF_ENFORCE", False,
        )
        self._csrf_patch.start()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)
        self._csrf_patch.stop()

    def test_happy_path_drops_key(self) -> None:
        os.environ["BOOTSTRAP_DELETE_ROUTE_KEY"] = "x"
        svc = PostRequestHandler()
        h, captured = _handler(
            "/api/envvars/delete", {"key": "BOOTSTRAP_DELETE_ROUTE_KEY"},
        )
        svc.handle(h)
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["status"], "deleted")
        self.assertEqual(
            captured["payload"]["key"], "BOOTSTRAP_DELETE_ROUTE_KEY",
        )
        self.assertTrue(captured["payload"]["existed"])
        self.assertNotIn("BOOTSTRAP_DELETE_ROUTE_KEY", os.environ)

    def test_missing_key_returns_400(self) -> None:
        svc = PostRequestHandler()
        h, captured = _handler("/api/envvars/delete", {})
        svc.handle(h)
        self.assertEqual(captured["status"], 400)
        self.assertIn("error", captured["payload"])
        self.assertIn("key", captured["payload"]["error"])

    def test_disallowed_prefix_returns_400(self) -> None:
        # PATH is a host var, not under any platform/service prefix —
        # the dashboard must not be able to clear it.
        svc = PostRequestHandler()
        h, captured = _handler("/api/envvars/delete", {"key": "PATH"})
        svc.handle(h)
        self.assertEqual(captured["status"], 400)
        self.assertIn("prefix", captured["payload"]["error"].lower())

    def test_idempotent_when_key_absent(self) -> None:
        os.environ.pop("BOOTSTRAP_TOTALLY_ABSENT", None)
        svc = PostRequestHandler()
        h, captured = _handler(
            "/api/envvars/delete", {"key": "BOOTSTRAP_TOTALLY_ABSENT"},
        )
        svc.handle(h)
        self.assertEqual(captured["status"], 200)
        self.assertFalse(captured["payload"]["existed"])


class DeleteEnvVarCsrfTests(unittest.TestCase):
    """CSRF gate on the delete route — the dispatcher must reject
    requests that lack a matching ``X-CSRF-Token`` header when CSRF
    is enforced (the production default for browser sessions)."""

    def test_csrf_enforced_rejects_missing_token(self) -> None:
        svc = PostRequestHandler()
        with patch(
            "media_stack.api.handlers_post._CSRF_ENFORCE", True,
        ):
            h, captured = _handler(
                "/api/envvars/delete", {"key": "BOOTSTRAP_FOO"},
            )
            svc.handle(h)
            self.assertEqual(captured["status"], 403)
            self.assertIn("CSRF", captured["payload"]["error"])

    def test_csrf_enforced_passes_when_header_matches_cookie(self) -> None:
        # The cookie's token must equal the X-CSRF-Token header
        # (double-submit). When they line up the dispatcher should
        # land on the success branch.
        orig_env = dict(os.environ)
        try:
            svc = PostRequestHandler()
            with patch(
                "media_stack.api.handlers_post._CSRF_ENFORCE", True,
            ):
                os.environ["BOOTSTRAP_CSRF_OK"] = "1"
                h, captured = _handler(
                    "/api/envvars/delete",
                    {"key": "BOOTSTRAP_CSRF_OK"},
                    cookie="media_stack_csrf=tok-abc",
                    csrf="tok-abc",
                )
                svc.handle(h)
                self.assertEqual(captured["status"], 200)
                self.assertEqual(
                    captured["payload"]["status"], "deleted",
                )
        finally:
            os.environ.clear()
            os.environ.update(orig_env)


if __name__ == "__main__":
    unittest.main()
