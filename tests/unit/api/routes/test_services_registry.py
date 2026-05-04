"""Tests for ``api/routes/services_registry.py`` (ADR-0007 Phase 2).

Covers each of the three migrated routes plus a routing-integration
sanity check that the Router auto-discovered + registered them all.

The first two routes delegate to legacy helpers in ``handlers_get``;
we mock those helpers' module-level imports so the tests assert
"this route delegates to the right helper" rather than re-testing
the helper's behaviour. The parameterized route's body is lifted
into the route module itself, so we mock at the registry layer
and assert the response shape directly — including the path-param
plumbing (``service_id`` flowing from URL through the Router into
the handler kwarg).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestServicesListing:
    """``GET /api/services`` -- the Apps-page listing. The route
    module owns a ``ServicesListingService`` constructor-injected
    via the route module's __init__, so the test patches the
    service's ``build()`` method through the singleton instance."""

    def test_delegates_to_listing_service(self) -> None:
        with patch(
            "media_stack.api.routes.services_registry"
            ".ServicesListingService.build",
            return_value=[{"id": "sonarr"}],
        ) as mock_build:
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/services")

        assert response.status == 200
        assert json.loads(response.body) == [{"id": "sonarr"}]
        mock_build.assert_called_once()


class TestServicesCategories:
    """``GET /api/services/categories`` -- the grouped-by-category
    view. Lifted body in the route module."""

    def test_delegates_to_categories_service(self) -> None:
        with patch(
            "media_stack.api.routes.services_registry"
            ".ServicesCategoriesService.build",
            return_value=[
                {"label": "Media", "ids": ["sonarr", "radarr"]},
                {"label": "Infrastructure", "ids": ["controller"]},
            ],
        ) as mock_build:
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch("GET", "/api/services/categories")

        assert response.status == 200
        body = json.loads(response.body)
        assert {c["label"] for c in body} == {"Media", "Infrastructure"}
        mock_build.assert_called_once()


class TestServiceApiKey:
    """``GET /api/services/{service_id}/api-key`` — the parameterized
    route. Body is lifted into the route module, so tests mock
    ``SERVICE_MAP`` + ``os.environ`` rather than a delegated helper.

    These tests also exercise the path-param plumbing end-to-end:
    the URL contains a real service id, the Router's regex
    captures it, and the handler receives it as the
    ``service_id`` kwarg.
    """

    def test_unknown_service_returns_404(self) -> None:
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {},
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/services/does-not-exist/api-key",
            )
        assert response.status == 404
        body = json.loads(response.body)
        assert "does-not-exist" in body["error"]

    def test_service_without_api_key_env_returns_404(self) -> None:
        svc = SimpleNamespace(api_key_env="")
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"qbittorrent": svc},
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/services/qbittorrent/api-key",
            )
        assert response.status == 404

    def test_service_with_unset_env_returns_no_key(self) -> None:
        svc = SimpleNamespace(api_key_env="SONARR_API_KEY")
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"sonarr": svc},
        ), patch.dict("os.environ", {"SONARR_API_KEY": ""}, clear=False):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/services/sonarr/api-key",
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "service": "sonarr",
            "env": "SONARR_API_KEY",
            "has_key": False,
            "key_preview": "",
        }

    def test_service_with_long_key_returns_masked_preview(self) -> None:
        svc = SimpleNamespace(api_key_env="SONARR_API_KEY")
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"sonarr": svc},
        ), patch.dict(
            "os.environ",
            {"SONARR_API_KEY": "abcd1234efgh5678"},
            clear=False,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/services/sonarr/api-key",
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["service"] == "sonarr"
        assert body["env"] == "SONARR_API_KEY"
        assert body["has_key"] is True
        assert body["key_preview"] == "abcd...5678"

    def test_service_with_short_key_returns_set_marker(self) -> None:
        svc = SimpleNamespace(api_key_env="SONARR_API_KEY")
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {"sonarr": svc},
        ), patch.dict(
            "os.environ", {"SONARR_API_KEY": "short"}, clear=False,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/services/sonarr/api-key",
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["has_key"] is True
        assert body["key_preview"] == "set"


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    services-registry domain. If a future change accidentally drops
    a handler from the registry, this test fires before any
    per-route test does."""

    def test_all_services_registry_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/services",
            "/api/services/categories",
            "/api/services/{service_id}/api-key",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing services-registry routes: {expected - registered}"
        )

    def test_parameterized_route_captures_service_id(self) -> None:
        """Sanity check: the Router's regex captures arbitrary
        path segments (with hyphens, digits, etc.) as
        ``service_id``. The handler returns 404 for an unknown id,
        which proves the kwarg flowed through."""
        with patch(
            "media_stack.api.services.registry.SERVICE_MAP",
            {},
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/services/some-arr-99/api-key",
            )
        assert response.status == 404
        body = json.loads(response.body)
        assert "some-arr-99" in body["error"]
