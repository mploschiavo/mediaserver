"""Tests for ``api/routes/stack_update.py`` (ADR-0007 Phase 2).

Covers the 5 migrated routes plus a routing-integration sanity
check that the Router auto-discovered + registered them all.

All five route methods delegate a single line to a service-layer
function (``stack_update.check_for_update`` /
``stack_update.upgrade_status`` / ``content.get_versions`` /
``content.get_downloads`` / ``content.get_stats``). We mock those
service calls and assert that the route plumbed the call through
correctly — including the parameterized-route case, where we
also assert the path-param flowed end-to-end (URL ->
Router regex -> ``task_id`` kwarg -> service call argument).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestStackUpdateRoute:
    """``GET /api/stack/update`` — registry probe."""

    @patch(
        "media_stack.api.services.stack_update.check_for_update",
    )
    def test_returns_service_payload_verbatim(self, mock_probe) -> None:
        mock_probe.return_value = {
            "current": "1.0.206",
            "latest": "1.0.207",
            "upgradable": True,
            "allow_inplace": True,
            "last_checked_epoch": 1777137476,
            "release_url": "https://example.test/v1.0.207",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/stack/update")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["current"] == "1.0.206"
        assert body["latest"] == "1.0.207"
        assert body["upgradable"] is True
        mock_probe.assert_called_once_with()

    @patch(
        "media_stack.api.services.stack_update.check_for_update",
    )
    def test_returns_no_update_state(self, mock_probe) -> None:
        mock_probe.return_value = {
            "current": "1.0.206",
            "latest": "1.0.206",
            "upgradable": False,
            "allow_inplace": True,
            "last_checked_epoch": 1777137476,
            "release_url": "",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/stack/update")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["upgradable"] is False
        assert body["latest"] == body["current"]


class TestStackUpgradeStatusRoute:
    """``GET /api/stack/upgrade/{task_id}`` — parameterized.

    The route receives ``task_id`` from the Router's regex match
    and forwards it to ``upgrade_status``. These tests pin both
    the response shape AND the path-param plumbing — if the
    Router ever stopped binding ``task_id``, the
    ``mock.assert_called_once_with(<id>)`` would fire."""

    @patch(
        "media_stack.api.services.stack_update.upgrade_status",
    )
    def test_running_task_returns_progress(self, mock_status) -> None:
        mock_status.return_value = {
            "task_id": "stack-upgrade-2026-04-25-14-30-22",
            "status": "running",
            "started_epoch": 1777137476,
            "target": "1.0.207",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET",
            "/api/stack/upgrade/stack-upgrade-2026-04-25-14-30-22",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "running"
        assert body["target"] == "1.0.207"
        mock_status.assert_called_once_with(
            "stack-upgrade-2026-04-25-14-30-22",
        )

    @patch(
        "media_stack.api.services.stack_update.upgrade_status",
    )
    def test_idle_state_when_no_task(self, mock_status) -> None:
        mock_status.return_value = {"status": "idle"}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/stack/upgrade/some-task-id",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"status": "idle"}
        mock_status.assert_called_once_with("some-task-id")

    @patch(
        "media_stack.api.services.stack_update.upgrade_status",
    )
    def test_stale_task_id_passthrough(self, mock_status) -> None:
        """If the caller polls with a stale task_id, the service
        returns ``{"status": "stale", "current_task": ...}``. We
        assert the route forwards the kwarg verbatim — including
        ids with hyphens + digits, which the Router's path-segment
        regex ``[^/]+`` accepts unchanged."""
        mock_status.return_value = {
            "status": "stale",
            "current_task": "stack-upgrade-2026-05-03-09-00-00",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET",
            "/api/stack/upgrade/stack-upgrade-2026-04-25-14-30-22",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "stale"
        mock_status.assert_called_once_with(
            "stack-upgrade-2026-04-25-14-30-22",
        )


class TestVersionsRoute:
    """``GET /api/versions`` — cached service version map."""

    @patch("media_stack.api.services.content.get_versions")
    def test_returns_versions_map(self, mock_versions) -> None:
        mock_versions.return_value = {
            "versions": {
                "sonarr": "4.0.14.2939",
                "radarr": "5.19.3.9730",
                "jellyfin": "10.10.6",
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/versions")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["versions"]["sonarr"] == "4.0.14.2939"
        assert body["versions"]["jellyfin"] == "10.10.6"
        # Service is called with the shared ``api_cache`` singleton.
        # We don't assert the cache identity, but we do pin the
        # arity — drift here would mean the route forgot to wire
        # in the cache.
        assert mock_versions.call_count == 1
        assert len(mock_versions.call_args.args) == 1


class TestDownloadsRoute:
    """``GET /api/downloads`` — uncached active-download snapshot."""

    @patch("media_stack.api.services.content.get_downloads")
    def test_returns_downloads_payload(self, mock_downloads) -> None:
        mock_downloads.return_value = {
            "qbittorrent": {
                "items": [
                    {"name": "show.s01e01", "progress": 45.2},
                ],
            },
            "sabnzbd": {"items": []},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/downloads")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["qbittorrent"]["items"][0]["progress"] == 45.2
        assert body["sabnzbd"]["items"] == []
        mock_downloads.assert_called_once_with()


class TestStatsRoute:
    """``GET /api/stats`` — cached per-arr library counts."""

    @patch("media_stack.api.services.content.get_stats")
    def test_returns_stats_map(self, mock_stats) -> None:
        mock_stats.return_value = {
            "stats": {
                "sonarr": {"count": 314, "label": "series"},
                "radarr": {"count": 1024, "label": "movies"},
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/stats")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["stats"]["sonarr"]["count"] == 314
        assert body["stats"]["radarr"]["label"] == "movies"
        assert mock_stats.call_count == 1
        assert len(mock_stats.call_args.args) == 1


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    stack-update domain. If a future change accidentally drops
    a handler from the registry, this test fires before any
    per-route test does."""

    def test_all_stack_update_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/stack/update",
            "/api/stack/upgrade/{task_id}",
            "/api/versions",
            "/api/downloads",
            "/api/stats",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing stack-update routes: {expected - registered}"
        )

    @patch(
        "media_stack.api.services.stack_update.upgrade_status",
    )
    def test_parameterized_route_captures_task_id(
        self, mock_status,
    ) -> None:
        """Sanity check: the Router's regex captures arbitrary
        path segments (with hyphens, digits, dots) as
        ``task_id``. Pinning this end-to-end means a Router
        refactor that broke path-param injection would fail
        loudly here, not silently in production."""
        mock_status.return_value = {"status": "idle"}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET",
            "/api/stack/upgrade/stack-upgrade-2026-05-03-09-00-00",
        )
        assert response.status == 200
        mock_status.assert_called_once_with(
            "stack-upgrade-2026-05-03-09-00-00",
        )
