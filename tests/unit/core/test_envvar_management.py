"""Tests for the env-var management CRUD surface — focuses on the
DELETE half (``POST /api/envvars/delete``), the partner of the
existing ``POST /api/envvars`` set handler.

What this file pins:

- ``DiagnosticsService.delete_envvar`` removes the key from
  ``os.environ`` and reports ``existed: true`` / ``existed: false``
  so the dashboard can render an idempotent confirmation rather
  than an error.
- The ``UserResourcesPostRoutes.handle_envvar_delete`` route
  rejects (a) missing ``key`` (400) and (b) keys outside the
  allowed prefix set (400) and (c) deletes an existing key on
  the happy path.

These tests intentionally do not exercise the HTTP transport — the
``UserResourcesPostRoutes.handle_envvar_delete`` route is invoked
directly with a ``MagicMock`` handler so we can inspect the response
shape.

ADR-0007 Phase 2 Phase E: tests previously drove the legacy
``PostRequestHandler.handle()`` chain; that's gone. CSRF gating
moved to ``server.do_POST`` (``_global_post_preflight``); the per-
route module owns the validation we exercise here.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.routes.post_user_resources import (  # noqa: E402
    UserResourcesPostRoutes,
)
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
    """``POST /api/envvars/delete`` route module — happy path + 400 cases."""

    def setUp(self) -> None:
        self._orig_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

    def _routes(self) -> UserResourcesPostRoutes:
        return UserResourcesPostRoutes()

    def test_happy_path_drops_key(self) -> None:
        os.environ["BOOTSTRAP_DELETE_ROUTE_KEY"] = "x"
        h, captured = _handler(
            "/api/envvars/delete", {"key": "BOOTSTRAP_DELETE_ROUTE_KEY"},
        )
        self._routes().handle_envvar_delete(h)
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["status"], "deleted")
        self.assertEqual(
            captured["payload"]["key"], "BOOTSTRAP_DELETE_ROUTE_KEY",
        )
        self.assertTrue(captured["payload"]["existed"])
        self.assertNotIn("BOOTSTRAP_DELETE_ROUTE_KEY", os.environ)

    def test_missing_key_returns_400(self) -> None:
        h, captured = _handler("/api/envvars/delete", {})
        self._routes().handle_envvar_delete(h)
        self.assertEqual(captured["status"], 400)
        self.assertIn("error", captured["payload"])
        self.assertIn("key", captured["payload"]["error"])

    def test_disallowed_prefix_returns_400(self) -> None:
        # PATH is a host var, not under any platform/service prefix —
        # the dashboard must not be able to clear it.
        h, captured = _handler("/api/envvars/delete", {"key": "PATH"})
        self._routes().handle_envvar_delete(h)
        self.assertEqual(captured["status"], 400)
        self.assertIn("prefix", captured["payload"]["error"].lower())

    def test_idempotent_when_key_absent(self) -> None:
        os.environ.pop("BOOTSTRAP_TOTALLY_ABSENT", None)
        h, captured = _handler(
            "/api/envvars/delete", {"key": "BOOTSTRAP_TOTALLY_ABSENT"},
        )
        self._routes().handle_envvar_delete(h)
        self.assertEqual(captured["status"], 200)
        self.assertFalse(captured["payload"]["existed"])


class DeleteEnvVarCsrfTests(unittest.TestCase):
    """CSRF gate -- now applied centrally in ``server._global_post_preflight``.

    The route module itself is not CSRF-aware; CSRF runs in
    ``server.do_POST`` BEFORE the Router dispatches. Tests here pin
    the CSRF predicate directly via ``_check_csrf``.
    """

    def test_csrf_enforced_rejects_missing_token(self) -> None:
        from media_stack.api.server import _check_csrf
        with mock.patch.dict(
            os.environ, {"CSRF_ENFORCE": "1"}, clear=False,
        ):
            h, _ = _handler(
                "/api/envvars/delete", {"key": "BOOTSTRAP_FOO"},
            )
            self.assertFalse(_check_csrf(h))

    def test_csrf_enforced_passes_when_header_matches_cookie(self) -> None:
        from media_stack.api.server import _check_csrf
        with mock.patch.dict(
            os.environ, {"CSRF_ENFORCE": "1"}, clear=False,
        ):
            h, _ = _handler(
                "/api/envvars/delete",
                {"key": "BOOTSTRAP_CSRF_OK"},
                cookie="media_stack_csrf=tok-abc",
                csrf="tok-abc",
            )
            self.assertTrue(_check_csrf(h))


if __name__ == "__main__":
    unittest.main()
