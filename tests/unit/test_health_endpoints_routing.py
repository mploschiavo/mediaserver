"""Routing tests for the new health/auto-heal GET + POST endpoints.

The single-dispatch handler in ``handlers_get.py`` /
``handlers_post.py`` makes it easy to typo-break a route without
the unit tests catching it. These tests confirm each new route
returns 200 and a parseable JSON body — no business logic asserted
here, that's covered by the per-service tests.

Endpoints exercised:

- ``GET  /api/health/config-integrity``
- ``GET  /api/health/crashloops``
- ``GET  /api/health/stories``
- ``GET  /api/auto-heal``
- ``POST /api/auto-heal/run``
- ``POST /api/auto-heal/enabled``"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# Reuse the helper from the existing handler test file.
from test_api_server_handlers import (  # noqa: E402
    make_handler,
    _get_json_written,
    _get_response_code,
)


class HealthEndpointsRoutingTests(unittest.TestCase):

    def _expect_200_json(self, handler) -> dict:
        code = _get_response_code(handler)
        self.assertEqual(code, 200, f"Expected 200, got {code}")
        body = _get_json_written(handler)
        self.assertIsInstance(body, dict)
        return body

    def test_config_integrity_endpoint_returns_services_dict(self) -> None:
        handler = make_handler("GET", "/api/health/config-integrity")
        # Patch the integrity service so we don't read /srv-config.
        with mock.patch(
            "media_stack.api.services.config_integrity.check_all",
            return_value={"prowlarr": {"status": "ok"}},
        ):
            handler.do_GET()
        body = self._expect_200_json(handler)
        self.assertIn("services", body)
        self.assertIn("checked_at", body)

    def test_crashloops_endpoint_returns_services_dict(self) -> None:
        handler = make_handler("GET", "/api/health/crashloops")
        with mock.patch(
            "media_stack.api.services.crashloop.check_all",
            return_value={"sonarr": {"cause": "healthy"}},
        ):
            handler.do_GET()
        body = self._expect_200_json(handler)
        self.assertIn("services", body)

    def test_stories_endpoint_returns_stories_list(self) -> None:
        handler = make_handler("GET", "/api/health/stories")
        with mock.patch(
            "media_stack.api.services.health_stories.compose_live",
            return_value={"stories": [], "checked_at": 0},
        ):
            handler.do_GET()
        body = self._expect_200_json(handler)
        self.assertIn("stories", body)
        self.assertIsInstance(body["stories"], list)

    def test_auto_heal_status_endpoint(self) -> None:
        handler = make_handler("GET", "/api/auto-heal")
        with mock.patch(
            "media_stack.api.services.auto_heal.status",
            return_value={"enabled": True, "recent_events": []},
        ):
            handler.do_GET()
        body = self._expect_200_json(handler)
        self.assertIn("enabled", body)
        self.assertIn("recent_events", body)

    def test_auto_heal_run_endpoint_post(self) -> None:
        handler = make_handler("POST", "/api/auto-heal/run", body="")
        with mock.patch(
            "media_stack.api.services.auto_heal.run_cycle",
            return_value={"snapshots_taken": 0, "heals_performed": []},
        ):
            handler.do_POST()
        body = self._expect_200_json(handler)
        self.assertIn("snapshots_taken", body)
        self.assertIn("heals_performed", body)

    def test_auto_heal_enabled_post(self) -> None:
        body = json.dumps({"enabled": False})
        handler = make_handler(
            "POST", "/api/auto-heal/enabled", body=body,
            headers={"Content-Type": "application/json"},
        )
        with mock.patch(
            "media_stack.api.services.auto_heal.set_enabled",
            return_value={"enabled": False},
        ) as m:
            handler.do_POST()
        m.assert_called_once_with(False)
        result = self._expect_200_json(handler)
        self.assertEqual(result, {"enabled": False})


if __name__ == "__main__":
    unittest.main()
