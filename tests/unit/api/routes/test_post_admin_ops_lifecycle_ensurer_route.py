"""Pins the ``POST /api/lifecycle-ensurers/{service}/{method}``
admin route to its ADR-0010 Phase 7 shape: the URL pair is
resolved to ``<service>:<method-kebab>`` and dispatched via
``run_job``. The legacy ``LifecycleEnsurerInvoker`` indirection is
gone; this test pins the new shape so a regression that re-routes
to the deleted invoker (or hardcodes a different Job-name shape)
fails in CI.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))


class _CapturingHandler:
    """Stand-in for ``http.server.BaseHTTPRequestHandler`` used by
    the route's ``_read_json_body`` / ``_json_response`` calls."""

    def __init__(self, body: dict | None = None) -> None:
        self._body = body or {}
        self.status: int | None = None
        self.response: dict | None = None

    def _read_json_body(self) -> dict:
        return self._body

    def _json_response(self, status: int, response: dict) -> None:
        self.status = status
        self.response = response


class _StubGate:
    def verify(self, _handler: Any) -> bool:
        return True

    def reject(self, _handler: Any) -> None:
        return None


class LifecycleEnsurerRouteTests(unittest.TestCase):
    """Pins URL → Job-name resolution + run_job dispatch for the
    admin ``/api/lifecycle-ensurers/{service}/{method}`` route."""

    def _module(self) -> Any:
        from media_stack.api.routes import post_admin_ops
        return post_admin_ops

    def _route_module_with_stub_gate(self) -> Any:
        mod = self._module()
        instance = mod.AdminOpsPostRoutes()
        instance._gate = _StubGate()  # noqa: SLF001
        return instance

    def test_url_resolves_to_kebab_job_name(self) -> None:
        captured: list[tuple[str, dict]] = []

        def fake_run_job(name: str, **kw: Any) -> dict:
            captured.append((name, kw))
            return {"status": "ok"}

        with patch(
            "media_stack.api.routes.post_admin_ops.run_job",
            fake_run_job,
        ):
            instance = self._route_module_with_stub_gate()
            handler = _CapturingHandler()
            instance.handle_lifecycle_ensurer_invoke(
                handler, service="radarr", method="ensure_jellyfin_notifier",
            )

        self.assertEqual(len(captured), 1)
        name, kw = captured[0]
        self.assertEqual(name, "radarr:ensure-jellyfin-notifier")
        self.assertEqual(kw.get("source"), "operator")
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response, {"status": "ok"})

    def test_body_source_overrides_default(self) -> None:
        captured_kwargs: dict[str, Any] = {}

        def fake_run_job(_name: str, **kw: Any) -> dict:
            captured_kwargs.update(kw)
            return {"status": "ok"}

        with patch(
            "media_stack.api.routes.post_admin_ops.run_job",
            fake_run_job,
        ):
            instance = self._route_module_with_stub_gate()
            handler = _CapturingHandler({"source": "auto-heal"})
            instance.handle_lifecycle_ensurer_invoke(
                handler, service="sonarr", method="ensure_indexers",
            )

        self.assertEqual(captured_kwargs.get("source"), "auto-heal")

    def test_unknown_job_returns_404_with_resolved_name(self) -> None:
        def fake_run_job(name: str, **_kw: Any) -> dict:
            return {"error": f"Unknown job: {name}", "known": []}

        with patch(
            "media_stack.api.routes.post_admin_ops.run_job",
            fake_run_job,
        ):
            instance = self._route_module_with_stub_gate()
            handler = _CapturingHandler()
            instance.handle_lifecycle_ensurer_invoke(
                handler,
                service="bogus",
                method="ensure_nothing",
            )

        self.assertEqual(handler.status, 404)
        self.assertIn("Unknown job", handler.response.get("error", ""))
        self.assertEqual(
            handler.response.get("job_name"),
            "bogus:ensure-nothing",
        )

    def test_run_job_exception_returns_500(self) -> None:
        def fake_run_job(_name: str, **_kw: Any) -> dict:
            raise RuntimeError("dispatch boom")

        with patch(
            "media_stack.api.routes.post_admin_ops.run_job",
            fake_run_job,
        ):
            instance = self._route_module_with_stub_gate()
            handler = _CapturingHandler()
            instance.handle_lifecycle_ensurer_invoke(
                handler,
                service="radarr",
                method="ensure_indexers",
            )

        self.assertEqual(handler.status, 500)
        self.assertIn("raised", handler.response.get("error", ""))


if __name__ == "__main__":
    unittest.main()
