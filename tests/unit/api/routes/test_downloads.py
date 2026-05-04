"""Tests for ``api/routes/downloads.py`` (ADR-0007 Phase 2 wave 3).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

We patch the module-level ``content_svc`` / ``config_svc``
references on the route module to assert "this route delegates to
the right service function" without re-testing the service's
behaviour.

Two download-related paths are intentionally NOT covered here
because they were migrated in earlier waves:

* ``/api/downloads`` lives in ``api/routes/stack_update.py``
  (covered by ``test_stack_update.py``).
* ``/api/download-history`` lives in
  ``api/routes/indexers_quality.py`` (covered by
  ``test_indexers_quality.py``).

Re-registering either would trip the Router's duplicate-path
guard.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestDownloadClientSettingsRoute:
    """``GET /api/download-client-settings`` — connection +
    queueing knobs for the configured download clients."""

    @patch("media_stack.api.routes.downloads.content_svc")
    def test_returns_client_settings(self, mock_content) -> None:
        mock_content.get_download_client_settings.return_value = {
            "torrent": {
                "max_active_downloads": 3,
                "max_active_torrents": 5,
                "max_active_uploads": 3,
                "dl_limit_mbps": 0,
                "up_limit_mbps": 0,
                "queueing_enabled": True,
            },
            "jellyfin_scan": {
                "state": "Idle",
                "last_status": "Completed",
                "interval_hours": 12,
                "task_id": "7738148ffcd07979c7ceb148e06b3aed",
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/download-client-settings")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["torrent"]["max_active_downloads"] == 3
        assert body["jellyfin_scan"]["state"] == "Idle"
        mock_content.get_download_client_settings.assert_called_once_with()

    @patch("media_stack.api.routes.downloads.content_svc")
    def test_passes_through_arbitrary_shape(self, mock_content) -> None:
        """Spec declares ``additionalProperties: true``; the route
        is a thin pass-through and must not mutate the payload.
        """
        mock_content.get_download_client_settings.return_value = {
            "unknown_section": {"future_field": 42},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/download-client-settings")

        assert response.status == 200
        assert json.loads(response.body) == {
            "unknown_section": {"future_field": 42},
        }


class TestDownloadCategoriesRoute:
    """``GET /api/download-categories`` — per-client category
    catalogue used by the *arr clients to route downloads."""

    @patch("media_stack.api.routes.downloads.config_svc")
    def test_returns_categories(self, mock_config) -> None:
        mock_config.get_download_categories.return_value = {
            "source": "qbittorrent",
            "categories": {
                "tv-sonarr": {"save_path": "/downloads/tv"},
                "movies-radarr": {"save_path": "/downloads/movies"},
            },
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/download-categories")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["source"] == "qbittorrent"
        assert "tv-sonarr" in body["categories"]
        mock_config.get_download_categories.assert_called_once_with()

    @patch("media_stack.api.routes.downloads.config_svc")
    def test_returns_unconfigured_state(self, mock_config) -> None:
        """When categories aren't configured the service emits a
        ``not_configured`` source + an empty map; the route must
        forward that shape unchanged.
        """
        mock_config.get_download_categories.return_value = {
            "source": "not_configured",
            "note": "Add categories in Config > Downloads",
            "categories": {},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/download-categories")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["source"] == "not_configured"
        assert body["categories"] == {}


class TestDownloadAnalyticsRoute:
    """``GET /api/download-analytics`` — aggregated history
    rollup (totals, per-service counts, top indexers, trend)."""

    @patch("media_stack.api.routes.downloads.content_svc")
    def test_returns_analytics_rollup(self, mock_content) -> None:
        mock_content.get_download_analytics.return_value = {
            "total_records": 20,
            "by_service": {"sonarr": 10, "radarr": 10},
            "top_indexers": [{"name": "YTS (Prowlarr)", "count": 10}],
            "daily_trend": [
                {"date": "2026-04-25", "count": 10},
                {"date": "2026-04-24", "count": 10},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/download-analytics")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["total_records"] == 20
        assert body["by_service"] == {"sonarr": 10, "radarr": 10}
        assert len(body["daily_trend"]) == 2
        mock_content.get_download_analytics.assert_called_once_with()

    @patch("media_stack.api.routes.downloads.content_svc")
    def test_returns_empty_rollup_when_no_history(
        self, mock_content,
    ) -> None:
        mock_content.get_download_analytics.return_value = {
            "total_records": 0,
            "by_service": {},
            "top_indexers": [],
            "daily_trend": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/download-analytics")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["total_records"] == 0
        assert body["daily_trend"] == []


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    downloads domain. If a future change accidentally drops a
    handler from the registry, this fires before any per-route
    test does.
    """

    def test_all_downloads_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/download-client-settings",
            "/api/download-categories",
            "/api/download-analytics",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing downloads routes: {expected - registered}"
        )

    def test_post_to_analytics_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        """``/api/download-analytics`` is GET-only; POST must 405."""
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/download-analytics")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
