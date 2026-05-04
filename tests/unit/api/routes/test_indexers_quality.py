"""Tests for ``api/routes/indexers_quality.py`` (ADR-0007 Phase 2).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

The five non-parameterized routes call into ``content_svc`` /
``quality_preset_service`` directly; we patch those module-level
references on the route module to assert "this route delegates
to the right service function" without re-testing the service's
behaviour. The two parameterized routes (``/api/quality-profiles``
+ ``/api/custom-formats``) are exercised end-to-end with a real
URL so the path-param plumbing (``service`` flowing from URL
through the Router into the handler kwarg) is also covered.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestIndexersRoute:
    """``GET /api/indexers`` — Prowlarr indexer list."""

    @patch("media_stack.api.routes.indexers_quality.content_svc")
    def test_returns_indexer_list(self, mock_content) -> None:
        mock_content.get_indexers.return_value = {
            "indexers": [
                {"id": 1, "name": "indexer-a", "enabled": True},
                {"id": 2, "name": "indexer-b", "enabled": False},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/indexers")

        assert response.status == 200
        assert json.loads(response.body) == {
            "indexers": [
                {"id": 1, "name": "indexer-a", "enabled": True},
                {"id": 2, "name": "indexer-b", "enabled": False},
            ],
        }
        mock_content.get_indexers.assert_called_once_with()

    @patch("media_stack.api.routes.indexers_quality.content_svc")
    def test_returns_empty_payload_when_no_indexers(
        self, mock_content,
    ) -> None:
        mock_content.get_indexers.return_value = {"indexers": []}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/indexers")

        assert response.status == 200
        assert json.loads(response.body) == {"indexers": []}


class TestIndexerStatsRoute:
    """``GET /api/indexer-stats`` — Prowlarr indexer counters."""

    @patch("media_stack.api.routes.indexers_quality.content_svc")
    def test_returns_indexer_stats(self, mock_content) -> None:
        mock_content.get_indexer_stats.return_value = {
            "stats": [
                {"indexer": "a", "queries": 12, "grabs": 3, "fails": 0},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/indexer-stats")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "stats": [
                {"indexer": "a", "queries": 12, "grabs": 3, "fails": 0},
            ],
        }
        mock_content.get_indexer_stats.assert_called_once_with()


class TestDownloadHistoryRoute:
    """``GET /api/download-history`` — recent Servarr download history."""

    @patch("media_stack.api.routes.indexers_quality.content_svc")
    def test_returns_recent_history(self, mock_content) -> None:
        mock_content.get_download_history.return_value = {
            "sonarr": [
                {"title": "Show.S01E01", "downloaded_at": "2026-05-01"},
            ],
            "radarr": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/download-history")

        assert response.status == 200
        body = json.loads(response.body)
        assert "sonarr" in body and "radarr" in body
        mock_content.get_download_history.assert_called_once_with()


class TestQualityPresetsRoute:
    """``GET /api/quality-presets`` — curated preset catalogue.

    The ``list_presets`` symbol is imported lazily inside the route
    method, so we patch it at the source module
    (``application.servarr.quality_preset_service``) — the
    ``services.apps.servarr.quality_preset_service`` shim aliases
    its ``sys.modules`` entry to that impl module, so a patch on
    either path resolves to the same callable.
    """

    @patch(
        "media_stack.application.servarr.quality_preset_service.list_presets",
    )
    def test_returns_preset_catalogue(self, mock_list) -> None:
        mock_list.return_value = {
            "presets": [
                {"id": "trash-1080p", "label": "Trash 1080p"},
                {"id": "trash-2160p", "label": "Trash 2160p"},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/quality-presets")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "presets": [
                {"id": "trash-1080p", "label": "Trash 1080p"},
                {"id": "trash-2160p", "label": "Trash 2160p"},
            ],
        }
        mock_list.assert_called_once_with()


class TestQualityProfilesForServiceRoute:
    """``GET /api/quality-profiles/{service}`` — parameterized
    route. Tests that the Router's path-param injection passes
    ``service`` through as a kwarg AND that the response shape
    flows back unchanged. The kwarg name matches the spec
    verbatim — lowercase ``service``, NOT camelCase.
    """

    @patch(
        "media_stack.application.servarr.quality_preset_service"
        ".get_current_profiles",
    )
    def test_returns_profiles_for_sonarr(self, mock_get) -> None:
        mock_get.return_value = {
            "service": "sonarr",
            "profiles": [{"id": 1, "name": "HD-1080p"}],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/quality-profiles/sonarr",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "service": "sonarr",
            "profiles": [{"id": 1, "name": "HD-1080p"}],
        }
        mock_get.assert_called_once_with("sonarr")

    @patch(
        "media_stack.application.servarr.quality_preset_service"
        ".get_current_profiles",
    )
    def test_passes_service_kwarg_for_radarr(self, mock_get) -> None:
        mock_get.return_value = {"service": "radarr", "profiles": []}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/quality-profiles/radarr",
        )

        assert response.status == 200
        mock_get.assert_called_once_with("radarr")

    @patch(
        "media_stack.application.servarr.quality_preset_service"
        ".get_current_profiles",
    )
    def test_service_with_dashes_passes_through(self, mock_get) -> None:
        """Path params accept any non-slash chars per the Router's
        regex — names with dashes / digits / dots all dispatch
        cleanly into the ``service`` kwarg.
        """
        mock_get.return_value = {"service": "readarr-audio", "profiles": []}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/quality-profiles/readarr-audio",
        )

        assert response.status == 200
        mock_get.assert_called_once_with("readarr-audio")


class TestCustomFormatsForServiceRoute:
    """``GET /api/custom-formats/{service}`` — parameterized route
    with the same path-param shape as quality-profiles.
    """

    @patch(
        "media_stack.application.servarr.quality_preset_service"
        ".get_custom_formats",
    )
    def test_returns_custom_formats_for_sonarr(self, mock_get) -> None:
        mock_get.return_value = {
            "service": "sonarr",
            "custom_formats": [{"id": 11, "name": "x265"}],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/custom-formats/sonarr",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "service": "sonarr",
            "custom_formats": [{"id": 11, "name": "x265"}],
        }
        mock_get.assert_called_once_with("sonarr")

    @patch(
        "media_stack.application.servarr.quality_preset_service"
        ".get_custom_formats",
    )
    def test_passes_service_kwarg_for_radarr(self, mock_get) -> None:
        mock_get.return_value = {"service": "radarr", "custom_formats": []}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/custom-formats/radarr",
        )

        assert response.status == 200
        mock_get.assert_called_once_with("radarr")


class TestArrWebhooksRoute:
    """``GET /api/arr-webhooks`` — ensure-and-return webhook map."""

    @patch("media_stack.api.routes.indexers_quality.content_svc")
    def test_returns_per_service_status_map(self, mock_content) -> None:
        mock_content.ensure_arr_scan_webhooks.return_value = {
            "sonarr": {"status": "ensured", "webhook_id": 5},
            "radarr": {"status": "ensured", "webhook_id": 7},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/arr-webhooks")

        assert response.status == 200
        body = json.loads(response.body)
        assert set(body) == {"sonarr", "radarr"}
        mock_content.ensure_arr_scan_webhooks.assert_called_once_with()


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    indexers + quality + arr-webhooks domain. If a future change
    accidentally drops a handler from the registry, this fires
    before any per-route test does.
    """

    def test_all_indexers_quality_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/indexers",
            "/api/indexer-stats",
            "/api/download-history",
            "/api/quality-presets",
            "/api/quality-profiles/{service}",
            "/api/custom-formats/{service}",
            "/api/arr-webhooks",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing indexers/quality routes: {expected - registered}"
        )

    def test_post_to_indexers_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/indexers")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_parameterized_route_captures_service_kwarg(self) -> None:
        """Sanity check: the Router's regex captures arbitrary
        path segments (with hyphens, digits, etc.) as ``service``
        and forwards them to the handler. The mock call assertion
        proves the kwarg flowed through.
        """
        with patch(
            "media_stack.application.servarr.quality_preset_service"
            ".get_current_profiles",
        ) as mock_get:
            mock_get.return_value = {"service": "lidarr-99", "profiles": []}
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/quality-profiles/lidarr-99",
            )
            assert response.status == 200
            mock_get.assert_called_once_with("lidarr-99")
