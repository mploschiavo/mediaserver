"""Tests for ``api/routes/routing_admin.py`` (ADR-0007 Phase 2 wave 4).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used in
production.

Strategy:

* The thin ``GET /api/routing`` route is unit-tested with a patched
  ``config_svc`` to assert pure delegation (the service layer's
  YAML-merging behaviour is covered in its own tests, not here).
* The four v2-pipeline routes are exercised against the *real*
  ``migrate_v1_to_v2`` function with a synthesised v1 dict so the
  migrator's output flows end-to-end through the route. Patching
  only ``config_svc.get_routing`` (the I/O boundary) keeps the
  tests behaviour-pinned without coupling them to the migrator's
  internal field defaults.
* Error-envelope tests inject a Mock for ``migrate_v1_to_v2`` that
  raises one of the narrowed exception classes (``AttributeError``,
  ``KeyError``, ``TypeError``, ``ValueError``); the route must
  convert each to a 500 + ``{"error": "..."}`` envelope rather than
  letting the exception propagate.

The ``_profile.media_server_id()`` lookup is covered by patching
``config_svc._profile`` to a Mock — the legacy chain's defensive
``except Exception`` is exercised via a side-effect that raises
``AttributeError``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from media_stack.api.routing import DispatchOutcome
from tests.unit.api.routes._helpers import RouteDispatchHarness


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Minimal v1 dict shape that satisfies ``migrate_v1_to_v2`` without
# tripping schema-validator errors. Mirrors what ``config_svc.get_routing``
# emits at startup before any operator overrides have been written.
_V1_ROUTING_FIXTURE = {
    "base_domain": "test.lan",
    "stack_subdomain": "media-stack",
    "gateway_host": "apps.media-stack.test.lan",
    "gateway_port": 80,
    "app_path_prefix": "/app",
    "strategy": "hybrid",
    "scheme": "",
    "internet_exposed": False,
    "direct_hosts": {
        "media_server": "jf.test.lan",
    },
}


def _patch_config_svc(monkeypatch_target: str = (
    "media_stack.api.routes.routing_admin.config_svc"
)):
    """Return a context manager patching ``config_svc`` on the route
    module so the v2 pipeline runs against the test fixture instead
    of touching the real profile YAML.
    """
    return patch(monkeypatch_target)


def _make_config_svc_mock(
    *, v1: dict | None = None, media_server_id: str = "jellyfin",
) -> MagicMock:
    """Build a ``config_svc`` mock that matches the surface the route
    module touches: ``get_routing`` + ``_profile.media_server_id``.
    """
    mock = MagicMock()
    mock.get_routing.return_value = (
        v1 if v1 is not None else dict(_V1_ROUTING_FIXTURE)
    )
    mock._profile.media_server_id.return_value = media_server_id
    return mock


# ---------------------------------------------------------------------------
# /api/routing — thin v1 read
# ---------------------------------------------------------------------------


class TestRoutingV1Route:
    """``GET /api/routing`` — flat v1 routing dict, thin delegation
    over ``config_svc.get_routing``.
    """

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_v1_routing_dict(self, mock_config) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["gateway_host"] == "apps.media-stack.test.lan"
        assert body["strategy"] == "hybrid"
        assert body["direct_hosts"] == {"media_server": "jf.test.lan"}
        mock_config.get_routing.assert_called_once_with()

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_does_not_swallow_keys(self, mock_config) -> None:
        """The route is a pure pass-through — every key from the
        service-layer dict reaches the response body unchanged.
        """
        mock_config.get_routing.return_value = {
            "base_domain": "x.io",
            "stack_subdomain": "y",
            "gateway_host": "g.x.io",
            "gateway_port": 443,
            "app_path_prefix": "/app",
            "strategy": "path",
            "scheme": "https",
            "internet_exposed": True,
            "direct_hosts": {},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["scheme"] == "https"
        assert body["internet_exposed"] is True


# ---------------------------------------------------------------------------
# /api/routing/v2 — migrated config + validation
# ---------------------------------------------------------------------------


class TestRoutingV2Route:
    """``GET /api/routing/v2`` — v1 → v2 migration + non-blocking
    validation array.
    """

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_v2_config_with_validation_array(
        self, mock_config,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/v2")

        assert response.status == 200
        body = json.loads(response.body)
        assert "config" in body
        assert "validation" in body
        # ``validation`` is always an array (may be empty); each
        # entry has the four-field shape the UI consumes.
        assert isinstance(body["validation"], list)
        for err in body["validation"]:
            assert set(err.keys()) == {"code", "field", "message", "hint"}

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_v2_config_has_migrated_strategy(self, mock_config) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/v2")

        body = json.loads(response.body)
        # ``strategy`` survives the v1 → v2 migration as a top-level
        # field on the v2 config dict.
        assert body["config"]["strategy"] == "hybrid"
        assert body["config"]["gateway_host"] == "apps.media-stack.test.lan"

    @patch(
        "media_stack.api.services.config.routing.migrate_v1_to_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_500_envelope_on_value_error(
        self, mock_config, mock_migrate,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        mock_migrate.side_effect = ValueError("bad strategy enum")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/v2")

        assert response.status == 500
        body = json.loads(response.body)
        assert "bad strategy enum" in body["error"]

    @patch(
        "media_stack.api.services.config.routing.migrate_v1_to_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_500_envelope_on_attribute_error(
        self, mock_config, mock_migrate,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        mock_migrate.side_effect = AttributeError("missing attr")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/v2")

        assert response.status == 500
        body = json.loads(response.body)
        assert "missing attr" in body["error"]

    @patch(
        "media_stack.api.services.config.routing.migrate_v1_to_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_truncates_long_error_to_200_chars(
        self, mock_config, mock_migrate,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        mock_migrate.side_effect = KeyError("x" * 500)
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/v2")

        assert response.status == 500
        body = json.loads(response.body)
        # ``str(KeyError(...))`` wraps the arg in quotes, but the
        # 200-char cap is what we're pinning here.
        assert len(body["error"]) == 200

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_recovers_when_media_server_id_lookup_fails(
        self, mock_config,
    ) -> None:
        """Defensive ``except (AttributeError, OSError)`` around the
        ``_profile.media_server_id()`` call: a missing attr or
        unreadable profile falls back to ``None`` (migrator's
        default), the route still emits a 200.
        """
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.side_effect = AttributeError(
            "no _profile",
        )
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/v2")

        assert response.status == 200
        body = json.loads(response.body)
        assert "config" in body


# ---------------------------------------------------------------------------
# /api/routing/routes — operator-facing route table
# ---------------------------------------------------------------------------


class TestRoutingRoutesTableRoute:
    """``GET /api/routing/routes`` — flat operator-friendly route
    table flattened from the v2 config.
    """

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_rows_and_summary(self, mock_config) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/routes")

        assert response.status == 200
        body = json.loads(response.body)
        assert "rows" in body
        assert "summary" in body
        assert isinstance(body["rows"], list)
        # Summary echoes the v2 config's strategy + gateway_host so
        # the UI can render a banner above the table.
        assert body["summary"]["strategy"] == "hybrid"
        assert body["summary"]["gateway_host"] == (
            "apps.media-stack.test.lan"
        )
        assert body["summary"]["app_path_prefix"] == "/app"
        assert "active_service_count" in body["summary"]

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_emits_catch_all_row(self, mock_config) -> None:
        """Every config emits at least the catch-all row — it's the
        last-resort path Envoy always falls through to.
        """
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/routes")

        body = json.loads(response.body)
        catch_all_rows = [
            r for r in body["rows"] if r["kind"] == "catch_all"
        ]
        assert len(catch_all_rows) == 1
        assert catch_all_rows[0]["match"] == "/ (catch-all)"

    @patch(
        "media_stack.api.services.config.routing.migrate_v1_to_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_500_envelope_on_pipeline_error(
        self, mock_config, mock_migrate,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        mock_migrate.side_effect = TypeError("bad type")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/routes")

        assert response.status == 500
        body = json.loads(response.body)
        assert "bad type" in body["error"]


# ---------------------------------------------------------------------------
# /api/routing/preview — Envoy + binding plan preview
# ---------------------------------------------------------------------------


class TestRoutingPreviewRoute:
    """``GET /api/routing/preview`` — pure-function preview of the
    Envoy ``route_config`` + the ``EdgeBindingAdapter``'s
    ``ApplyPlan`` for the current v2 config.
    """

    @patch(
        "media_stack.services.edge.k8s_ingress_adapter.K8sIngressAdapter",
    )
    @patch(
        "media_stack.services.edge.envoy_route_generator_v2"
        ".generate_route_config_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_envoy_route_config_and_binding_plan(
        self, mock_config, mock_generate, mock_adapter_cls,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        mock_generate.return_value = {
            "name": "rc",
            "virtual_hosts": [{"name": "vh1"}, {"name": "vh2"}],
        }
        mock_step = MagicMock()
        mock_step.kind = "ingress.apply"
        mock_step.description = "apply Ingress 'media'"
        mock_step.payload = {"apiVersion": "networking.k8s.io/v1"}
        mock_plan = MagicMock()
        mock_plan.steps = [mock_step]
        mock_plan.warnings = ["deprecated annotation"]
        mock_adapter = MagicMock()
        mock_adapter.compute_apply_plan.return_value = mock_plan
        mock_adapter_cls.return_value = mock_adapter

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/preview")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["envoy"]["route_config"]["name"] == "rc"
        assert body["envoy"]["vhost_count"] == 2
        assert body["binding"]["adapter"] == "k8s_ingress"
        assert body["binding"]["steps"] == [{
            "kind": "ingress.apply",
            "description": "apply Ingress 'media'",
            "payload": {"apiVersion": "networking.k8s.io/v1"},
        }]
        assert body["binding"]["warnings"] == ["deprecated annotation"]

    @patch(
        "media_stack.services.edge.envoy_route_generator_v2"
        ".generate_route_config_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_500_envelope_on_generator_error(
        self, mock_config, mock_generate,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        mock_generate.side_effect = ValueError("bad cfg shape")

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/preview")

        assert response.status == 500
        body = json.loads(response.body)
        assert "bad cfg shape" in body["error"]


# ---------------------------------------------------------------------------
# /api/routing/effective — defaults merged into hosts
# ---------------------------------------------------------------------------


class TestRoutingEffectiveRoute:
    """``GET /api/routing/effective`` — same shape as ``/v2`` but
    with ``defaults`` merged into each host's per-field knobs.
    """

    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_effective_config(self, mock_config) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/effective")

        assert response.status == 200
        body = json.loads(response.body)
        assert "config" in body
        # Hosts is always a list (may be empty when no direct_hosts
        # are configured in the fixture); each entry has the merged
        # defaults applied.
        assert isinstance(body["config"].get("hosts", []), list)

    @patch(
        "media_stack.api.services.config.routing.migrate_v1_to_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_merges_defaults_into_hosts(
        self, mock_config, mock_migrate,
    ) -> None:
        """The merge fills in falsy-or-missing per-host fields from
        ``defaults``; explicit per-host values are left alone.
        """
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        # Hand-rolled cfg-like object with a ``to_dict`` returning a
        # synthesised v2 dict shape — the route only reads ``to_dict``,
        # so a MagicMock is sufficient and avoids coupling to the real
        # schema's defaults.
        cfg_mock = MagicMock()
        cfg_mock.to_dict.return_value = {
            "defaults": {
                "websocket": True,
                "timeout_seconds": 30,
                "body_limit_mb": 25,
                "auth": {"required": True},
                "headers": {"X-Default": "1"},
            },
            "hosts": [
                # Empty: every default applies.
                {"canonical": "a.test", "service_id": "sa"},
                # Explicit overrides win — the merge leaves them.
                {
                    "canonical": "b.test", "service_id": "sb",
                    "websocket": False, "timeout_seconds": 60,
                    "body_limit_mb": 100,
                    "auth": {"required": False},
                    "headers": {"X-Host": "1"},
                },
            ],
        }
        mock_migrate.return_value = cfg_mock

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/effective")

        assert response.status == 200
        body = json.loads(response.body)
        hosts = body["config"]["hosts"]
        # First host: every default was merged in.
        assert hosts[0]["websocket"] is True
        assert hosts[0]["timeout_seconds"] == 30
        assert hosts[0]["body_limit_mb"] == 25
        assert hosts[0]["auth"] == {"required": True}
        assert hosts[0]["headers"] == {"X-Default": "1"}
        # Second host: explicit values preserved.
        # ``websocket=False`` is falsy so the merge replaces it with
        # the default (``True``) — this matches the legacy chain's
        # ``if not h.get("websocket")`` shape.
        assert hosts[1]["websocket"] is True  # falsy override -> default
        assert hosts[1]["timeout_seconds"] == 60
        assert hosts[1]["body_limit_mb"] == 100
        assert hosts[1]["auth"] == {"required": False}
        assert hosts[1]["headers"] == {"X-Host": "1"}

    @patch(
        "media_stack.api.services.config.routing.migrate_v1_to_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_uses_route_local_fallback_when_defaults_empty(
        self, mock_config, mock_migrate,
    ) -> None:
        """When ``defaults`` is empty, the merge uses the route-local
        fallback constants (``_EFFECTIVE_*_DEFAULT``).
        """
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        cfg_mock = MagicMock()
        cfg_mock.to_dict.return_value = {
            "defaults": {},
            "hosts": [
                {"canonical": "a.test", "service_id": "sa"},
            ],
        }
        mock_migrate.return_value = cfg_mock

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/effective")

        assert response.status == 200
        body = json.loads(response.body)
        host = body["config"]["hosts"][0]
        assert host["websocket"] is False
        assert host["timeout_seconds"] == 0
        assert host["body_limit_mb"] == 0
        # ``auth`` + ``headers`` get filled only when defaults
        # supplies them; an empty defaults leaves them missing.
        assert "auth" not in host
        assert "headers" not in host

    @patch(
        "media_stack.api.services.config.routing.migrate_v1_to_v2",
    )
    @patch("media_stack.api.routes.routing_admin.config_svc")
    def test_returns_500_envelope_on_pipeline_error(
        self, mock_config, mock_migrate,
    ) -> None:
        mock_config.get_routing.return_value = dict(_V1_ROUTING_FIXTURE)
        mock_config._profile.media_server_id.return_value = "jellyfin"
        mock_migrate.side_effect = ValueError("schema drift")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing/effective")

        assert response.status == 500
        body = json.loads(response.body)
        assert "schema drift" in body["error"]


# ---------------------------------------------------------------------------
# Auto-discovery + spec parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    routing-admin domain. If a future change accidentally drops a
    handler from the registry, this fires before any per-route test
    does.
    """

    def test_all_routing_admin_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/routing",
            "/api/routing/v2",
            "/api/routing/routes",
            "/api/routing/preview",
            "/api/routing/effective",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing routing-admin routes: {expected - registered}"
        )

    def test_post_to_routing_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        """``POST /api/routing`` is still served by the legacy
        handlers_post chain (PR-5 mutation lands separately); the
        Router only registers the GET, so a POST against this path
        falls through to METHOD_NOT_ALLOWED in the harness which
        only sees GET-tagged registrations.
        """
        harness = RouteDispatchHarness.with_default_router()
        # The harness's dispatcher only knows about GET registrations
        # for this path — POST is handled by handlers_post in
        # production but isn't registered on the Router yet, so the
        # outcome is METHOD_NOT_ALLOWED.
        outcome, _ = harness.try_dispatch("POST", "/api/routing/v2")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_unregistered_subpath_returns_no_match(self) -> None:
        """Sanity check: a sibling path under ``/api/routing/`` that
        isn't registered falls through to NO_MATCH so the legacy
        chain can take over.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch(
            "GET", "/api/routing/not-a-real-route",
        )
        assert outcome == DispatchOutcome.NO_MATCH
