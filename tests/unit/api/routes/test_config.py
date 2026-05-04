"""Tests for ``api/routes/config.py`` (ADR-0007 Phase 2 wave 4).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

All six routes delegate to ``config_svc.<func>()``; we patch the
``config_svc`` module reference on the route module to assert
"this route delegates to the right service function" without
re-testing the service layer's behaviour.

The ``/api/envvars`` route is the security-sensitive one — its
test pins that whatever the (already-redacted) service returns
flows through the route untouched, i.e. the route handler does
NOT reshape, drop fields, or otherwise risk leaking a value the
service deliberately masked.

The ``/api/config-drift`` route wraps the service call in
``api_cache.get_or_compute(...)``. We patch the cache singleton
on the route module to assert: (a) the cache is consulted, (b)
the cache key + TTL match the legacy chain (``"config_drift"``
+ 60s), and (c) the cached value flows through unchanged when
hit. No real Docker / k8s API calls are made because the cache
``get_or_compute`` is mocked.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class TestEnvRoute:
    """``GET /api/env`` — runtime environment fingerprint."""

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_runtime_env_payload(self, mock_config) -> None:
        mock_config.get_env.return_value = {
            "namespace": "media-stack",
            "profile_name": "media-stack.profile.yaml",
            "node_ip": "10.0.0.7",
            "node_ips": ["10.0.0.7"],
            "node_count": 1,
            "platform": "Linux-6.17.0-22-generic-x86_64",
            "python": "3.13.0",
            "runtime": "kubernetes",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/env")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["namespace"] == "media-stack"
        assert body["runtime"] == "kubernetes"
        assert body["node_count"] == 1
        mock_config.get_env.assert_called_once_with()

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_compose_runtime(self, mock_config) -> None:
        mock_config.get_env.return_value = {
            "namespace": "",
            "profile_name": "",
            "node_ip": "",
            "node_ips": [],
            "node_count": 0,
            "platform": "Linux",
            "python": "3.13.0",
            "runtime": "compose",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/env")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["runtime"] == "compose"


class TestEnvVarsRoute:
    """``GET /api/envvars`` — sanitised env var dump.

    Security contract: secret-suffixed values are masked to ``***``
    by the service before they leave the process. The route handler
    delegates straight to the masked dict — these tests pin that
    nothing in the route reshapes the dict in a way that could
    leak a redacted value.
    """

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_sanitised_envvars(self, mock_config) -> None:
        mock_config.get_envvars.return_value = {
            "BOOTSTRAP_PROFILE_FILE": "/etc/profile.yaml",
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "***",
            "SONARR_API_KEY": "***",
            "TZ": "America/New_York",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envvars")

        assert response.status == 200
        body = json.loads(response.body)
        # Plaintext fields flow through untouched.
        assert body["STACK_ADMIN_USERNAME"] == "admin"
        assert body["TZ"] == "America/New_York"
        # Already-masked secrets stay masked — the route MUST NOT
        # un-mask values the service deliberately redacted.
        assert body["STACK_ADMIN_PASSWORD"] == "***"
        assert body["SONARR_API_KEY"] == "***"
        mock_config.get_envvars.assert_called_once_with()

    @patch("media_stack.api.routes.config.config_svc")
    def test_passes_through_empty_dict(self, mock_config) -> None:
        mock_config.get_envvars.return_value = {}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envvars")

        assert response.status == 200
        assert json.loads(response.body) == {}

    @patch("media_stack.api.routes.config.config_svc")
    def test_route_does_not_reshape_or_filter(self, mock_config) -> None:
        """The route returns whatever the service returned, byte-for-byte.

        Pinning this protects against a future "helpfully" added
        filter on the route side that might unmask a value the
        service deliberately redacted. The redaction contract lives
        in the service layer; the route is a pass-through.
        """
        sentinel = {
            "AUTHELIA_JWT_SECRET": "***",
            "AUTHELIA_SESSION_SECRET": "***",
            "AUTHELIA_STORAGE_ENCRYPTION_KEY": "***",
            "K8S_NAMESPACE": "media-stack",
        }
        mock_config.get_envvars.return_value = sentinel
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/envvars")

        assert response.status == 200
        assert json.loads(response.body) == sentinel


class TestManifestsRoute:
    """``GET /api/manifests`` — deployment manifest descriptor."""

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_kubernetes_manifest(self, mock_config) -> None:
        mock_config.get_manifests.return_value = {
            "type": "kubernetes",
            "namespace": "media-stack",
            "deployments": 12,
            "services": [
                {"name": "controller", "image": "harbor/foo:1.0"},
                {"name": "ui", "image": "harbor/ui:1.0"},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/manifests")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["type"] == "kubernetes"
        assert body["deployments"] == 12
        mock_config.get_manifests.assert_called_once_with()

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_compose_manifest(self, mock_config) -> None:
        mock_config.get_manifests.return_value = {
            "type": "compose",
            "file": "/compose/docker-compose.yml",
            "content": "version: '3.8'\nservices: {}\n",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/manifests")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["type"] == "compose"

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_unknown_manifest_fallback(self, mock_config) -> None:
        mock_config.get_manifests.return_value = {
            "type": "unknown",
            "content": None,
            "error": "No manifest found.",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/manifests")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["type"] == "unknown"


class TestConfigDriftRoute:
    """``GET /api/config-drift`` — cached drift report.

    The route wraps ``config_svc.get_config_drift`` in
    ``api_cache.get_or_compute(...)`` with key ``config_drift`` and
    a 60s TTL. These tests pin (a) the cache is consulted, (b) the
    args match the legacy chain, and (c) the cached value flows
    through unchanged.
    """

    @patch("media_stack.api.routes.config.api_cache")
    @patch("media_stack.api.routes.config.config_svc")
    def test_consults_cache_with_60s_ttl(
        self, mock_config, mock_cache,
    ) -> None:
        mock_cache.get_or_compute.return_value = {
            "clean": True,
            "total": 0,
            "drifts": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/config-drift")

        assert response.status == 200
        # Cache call shape pinned: key + compute_fn + ttl.
        mock_cache.get_or_compute.assert_called_once_with(
            "config_drift", mock_config.get_config_drift, ttl=60,
        )

    @patch("media_stack.api.routes.config.api_cache")
    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_drift_payload_from_cache(
        self, mock_config, mock_cache,
    ) -> None:
        mock_cache.get_or_compute.return_value = {
            "clean": False,
            "total": 2,
            "drifts": [
                {
                    "area": "routing",
                    "key": "base_domain",
                    "expected": "local",
                    "actual": "io",
                },
                {
                    "area": "api_key",
                    "key": "sonarr",
                    "expected": "abcd...wxyz",
                    "actual": "1234...5678",
                },
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/config-drift")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["clean"] is False
        assert body["total"] == 2
        assert len(body["drifts"]) == 2

    @patch("media_stack.api.routes.config.api_cache")
    @patch("media_stack.api.routes.config.config_svc")
    def test_does_not_call_service_directly(
        self, mock_config, mock_cache,
    ) -> None:
        """The route MUST go through the cache, NEVER call
        ``config_svc.get_config_drift()`` directly. The legacy
        contract is "cache on; expensive Docker/k8s calls run
        at most once per 60s". A direct call would defeat the
        rate-limiting story.
        """
        mock_cache.get_or_compute.return_value = {
            "clean": True, "total": 0, "drifts": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/config-drift")

        assert response.status == 200
        mock_config.get_config_drift.assert_not_called()


class TestConfigLibrariesRoute:
    """``GET /api/config/libraries`` — canonical library config."""

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_libraries(self, mock_config) -> None:
        mock_config.get_libraries.return_value = {
            "media_server": "jellyfin",
            "source": "profile",
            "libraries": [
                {
                    "name": "Movies",
                    "collection_type": "movies",
                    "paths": ["/media/movies"],
                },
                {
                    "name": "TV Shows",
                    "collection_type": "tvshows",
                    "paths": ["/media/tv"],
                },
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/config/libraries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["media_server"] == "jellyfin"
        assert len(body["libraries"]) == 2
        mock_config.get_libraries.assert_called_once_with()

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_defaults_when_unconfigured(self, mock_config) -> None:
        mock_config.get_libraries.return_value = {
            "media_server": "jellyfin",
            "source": "defaults",
            "libraries": [],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/config/libraries")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["source"] == "defaults"
        assert body["libraries"] == []


class TestMetadataSettingsRoute:
    """``GET /api/metadata-settings`` — metadata-preset config."""

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_metadata_settings(self, mock_config) -> None:
        mock_config.get_metadata_settings.return_value = {
            "language": "en",
            "agents": ["TheMovieDB", "TheTVDB"],
            "image_preferences": {"prefer_local": True},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/metadata-settings")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["language"] == "en"
        assert "TheMovieDB" in body["agents"]
        mock_config.get_metadata_settings.assert_called_once_with()

    @patch("media_stack.api.routes.config.config_svc")
    def test_returns_empty_settings(self, mock_config) -> None:
        mock_config.get_metadata_settings.return_value = {}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/metadata-settings")

        assert response.status == 200
        assert json.loads(response.body) == {}


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the Config
    domain. If a future change accidentally drops a handler from
    the registry, this fires before any per-route test does.
    """

    def test_all_config_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/env",
            "/api/envvars",
            "/api/manifests",
            "/api/config-drift",
            "/api/config/libraries",
            "/api/metadata-settings",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing config routes: {expected - registered}"
        )

    def test_post_to_env_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/env")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_envvars_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        """``/api/envvars`` ALSO accepts POST in the legacy chain
        (set_envvar) — but that's a separate spec entry. The GET
        registration here only owns the GET verb; POST is
        registered elsewhere or falls through to the legacy chain.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("PUT", "/api/envvars")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_config_drift_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/config-drift")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
