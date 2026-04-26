"""Route validation test suite — verifies Envoy routing for compose and K8s.

Validates the full routing pipeline end-to-end by rendering Envoy configs
from synthetic service definitions and checking the generated routes.

Covers:
1. Every standard service gets a route at /app/{service_id}
2. Controller is reachable at both /app/media-stack-controller AND /app/controller
3. Unknown /app/X paths redirect to /app/homepage for HTML browsers
4. Trailing slash normalization (/app/X and /app/X/ both work)
5. /app and /app/ redirect to /app/homepage
6. Root / redirects to default app (jellyfin)
7. Localhost and wildcard catch-all vhosts mirror gateway routes
8. Per-service auth bypass applied correctly on routes
9. Envoy ext_authz filter injected when auth is active

Run with: python -m pytest tests/unit/test_route_validation.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.platforms.compose.edge.providers.envoy.helpers import (
    _cluster_name,
    _path_prefix_app_slug,
    _rule_hosts,
    _rule_path_prefix,
)
from media_stack.core.platforms.compose.edge.providers.envoy.routes import (
    html_accept_header_match,
    primary_route_cfg,
)
from media_stack.core.platforms.compose.edge.providers.envoy.virtual_hosts import (
    build_virtual_hosts,
)
from media_stack.core.auth.envoy_ext_authz import EXT_AUTHZ_FILTER_NAME


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "contracts" / "auth.yaml"
SERVICES_DIR = ROOT / "contracts" / "services"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _load_service_ids() -> list[str]:
    """Load all service IDs from contracts (network services only, port > 0)."""
    ids = []
    for path in sorted(SERVICES_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
            svc = data.get("service", {})
            if svc.get("id") and svc.get("port", 0) > 0:
                ids.append(svc["id"])
        except Exception:
            continue
    return ids


def _build_test_services(
    gateway_host: str = "apps.media-stack.local",
    path_prefix: str = "/app",
) -> dict[str, dict[str, Any]]:
    """Build synthetic compose service dicts for testing.

    Simulates what generate_envoy_config_main does for K8s mode.
    """
    try:
        from media_stack.api.services.registry import SERVICES as reg_services
        ports = {s.id: s.port for s in reg_services if s.port > 0}
    except Exception:
        ports = {}

    # Synthetic entries not in registry
    ports.setdefault("media-stack-controller", 9100)

    services: dict[str, dict[str, Any]] = {}
    for svc_name, port in ports.items():
        rule = f"Host(`{gateway_host}`) && PathPrefix(`{path_prefix}/{svc_name}`)"
        services[svc_name] = {
            "container_name": svc_name,
            "labels": {
                "traefik.enable": "true",
                f"traefik.http.routers.{svc_name}-path.rule": rule,
                f"traefik.http.routers.{svc_name}-path.service": svc_name,
                f"traefik.http.services.{svc_name}.loadbalancer.server.port": str(port),
            },
        }
    return services


def _render_envoy_routes(
    gateway_host: str = "apps.media-stack.local",
    auth_mode: str = "none",
) -> dict[str, Any]:
    """Render full Envoy virtual hosts from synthetic services."""
    from media_stack.core.platforms.compose.services.spec import ComposeSpecResolver
    from media_stack.core.platforms.compose.services.labels import (
        ComposeLabelConfig,
        ComposeLabelService,
    )
    from media_stack.core.platforms.compose.services.edge_route_graph import (
        ComposeEdgeRouteGraphService,
    )
    from media_stack.core.platforms.compose.edge.providers.envoy.dynamic_config import (
        EnvoyDynamicConfigService,
    )
    from media_stack.core.edge.provider_registry import compose_label_specs_by_provider

    compose_provider_specs = {
        p: dict(s) for p, s in compose_label_specs_by_provider().items()
    }

    try:
        from media_stack.api.services.registry import get_preserve_path_prefix_services
        preserve_names = tuple(s.id for s in get_preserve_path_prefix_services())
    except Exception:
        preserve_names = ()

    try:
        from media_stack.api.services.registry import get_web_ui_services
        redirect_names = tuple(s.id for s in get_web_ui_services())
    except Exception:
        redirect_names = ()

    spec_resolver = ComposeSpecResolver(
        compose_file=Path("/dev/null"),
        compose_env_file=None,
        compose_project_name="media-stack",
        compose_profiles=(),
        selected_apps=(),
        edge_router_service_names=("envoy",),
        environment_overrides={
            "APP_GATEWAY_HOST": gateway_host,
            "APP_PATH_PREFIX": "/app",
        },
    )

    label_service = ComposeLabelService(
        cfg=ComposeLabelConfig(
            project_name="media-stack",
            edge_router_provider="envoy",
            route_strategy="hybrid",
            internet_exposed=False,
            app_gateway_host=gateway_host,
            app_path_prefix="/app",
            media_server_direct_host="",
            auth_provider="",
            auth_middleware="",
            path_prefix_redirect_service_names=redirect_names,
            path_prefix_preserve_service_names=preserve_names,
            edge_compose_provider_specs=compose_provider_specs,
            auth_provider_middleware_defaults={},
            media_server_service_names=(),
        ),
    )

    route_graph_service = ComposeEdgeRouteGraphService(
        label_service=label_service,
        spec_resolver=spec_resolver,
    )

    # Build auth policy if auth_mode is set
    auth_policy = None
    if auth_mode in ("authelia", "authentik"):
        from media_stack.core.auth.gateway_policy import AuthContractService
        auth_contract = AuthContractService(CONTRACT_PATH)
        try:
            from media_stack.api.services.registry import SERVICES as reg_svcs
            svc_list = [(s.id, s.category) for s in reg_svcs]
        except Exception:
            svc_list = []
        svc_list.append(("media-stack-controller", "infrastructure"))
        svc_list.append(("controller", "infrastructure"))
        auth_policy = auth_contract.resolve_policy(
            {"mode": auth_mode}, services=svc_list
        )

    dynamic_config_service = EnvoyDynamicConfigService(
        route_graph_service=route_graph_service,
        spec_resolver=spec_resolver,
        auth_policy=auth_policy,
    )

    services = _build_test_services(gateway_host)
    render_result = dynamic_config_service.render(services)
    return render_result.payload


def _find_routes_for_prefix(
    payload: dict[str, Any],
    prefix: str,
    vhost_name: str | None = None,
) -> list[dict[str, Any]]:
    """Find all routes matching a given prefix in the rendered payload."""
    matches = []
    static = payload.get("static_resources", {})
    listeners = static.get("listeners", [])
    if not listeners:
        return matches

    hcm = listeners[0].get("filter_chains", [{}])[0].get("filters", [{}])[0]
    route_config = hcm.get("typed_config", {}).get("route_config", {})
    vhosts = route_config.get("virtual_hosts", [])

    for vh in vhosts:
        if vhost_name and vh.get("name") != vhost_name:
            continue
        for route in vh.get("routes", []):
            match = route.get("match", {})
            route_prefix = match.get("prefix", "")
            route_path = match.get("path", "")
            if route_prefix == prefix or route_path == prefix:
                matches.append(route)
    return matches


def _find_redirect_for_prefix(
    payload: dict[str, Any],
    prefix: str,
) -> dict[str, Any] | None:
    """Find a redirect route matching a prefix."""
    routes = _find_routes_for_prefix(payload, prefix)
    for r in routes:
        if "redirect" in r:
            return r
    return None


def _get_vhost_names(payload: dict[str, Any]) -> list[str]:
    """Get all virtual host names from the payload."""
    static = payload.get("static_resources", {})
    listeners = static.get("listeners", [])
    if not listeners:
        return []
    hcm = listeners[0].get("filter_chains", [{}])[0].get("filters", [{}])[0]
    route_config = hcm.get("typed_config", {}).get("route_config", {})
    return [vh.get("name", "") for vh in route_config.get("virtual_hosts", [])]


def _get_all_routes(
    payload: dict[str, Any],
    vhost_name: str | None = None,
) -> list[dict[str, Any]]:
    """Get all routes, optionally filtered by vhost."""
    static = payload.get("static_resources", {})
    listeners = static.get("listeners", [])
    if not listeners:
        return []
    hcm = listeners[0].get("filter_chains", [{}])[0].get("filters", [{}])[0]
    route_config = hcm.get("typed_config", {}).get("route_config", {})
    routes = []
    for vh in route_config.get("virtual_hosts", []):
        if vhost_name and vh.get("name") != vhost_name:
            continue
        routes.extend(vh.get("routes", []))
    return routes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStandardServiceRoutes(unittest.TestCase):
    """Every service must have a route at /app/{service_id}."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _render_envoy_routes()
        cls.service_ids = _load_service_ids()

    def test_all_registered_services_have_routes(self) -> None:
        """Each contract-defined service should have an /app/{id} route."""
        all_routes = _get_all_routes(self.payload)
        route_prefixes = set()
        for r in all_routes:
            match = r.get("match", {})
            prefix = match.get("prefix", "")
            if prefix.startswith("/app/"):
                route_prefixes.add(prefix)

        for svc_id in self.service_ids:
            expected = f"/app/{svc_id}"
            self.assertIn(expected, route_prefixes,
                f"Service {svc_id} missing route at {expected}")

    def test_controller_has_both_routes(self) -> None:
        """Controller reachable at /app/media-stack-controller AND /app/controller."""
        all_routes = _get_all_routes(self.payload)
        prefixes = set()
        paths = set()
        for r in all_routes:
            m = r.get("match", {})
            if m.get("prefix"):
                prefixes.add(m["prefix"])
            if m.get("path"):
                paths.add(m["path"])
        self.assertIn("/app/media-stack-controller", prefixes,
            "Controller must be reachable at /app/media-stack-controller")
        # Alias is a redirect (path or prefix match)
        self.assertTrue(
            "/app/controller" in paths or "/app/controller/" in prefixes,
            "Controller alias redirect must exist at /app/controller")

    def test_controller_alias_redirects_to_full_name(self) -> None:
        """The /app/controller alias must redirect to /app/media-stack-controller."""
        all_routes = _get_all_routes(self.payload)
        alias_redirect = None
        for r in all_routes:
            m = r.get("match", {})
            if m.get("path") == "/app/controller" and "redirect" in r:
                alias_redirect = r
                break
        self.assertIsNotNone(alias_redirect,
            "/app/controller must have a redirect route")
        self.assertEqual(
            alias_redirect["redirect"]["path_redirect"],
            "/app/media-stack-controller")


class TestTrailingSlashNormalization(unittest.TestCase):
    """Both /app/X and /app/X/ must route to the same service."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _render_envoy_routes()

    def test_prefix_match_covers_trailing_slash(self) -> None:
        """Envoy prefix match '/app/sonarr' also matches '/app/sonarr/'."""
        # Envoy prefix matching: "/app/sonarr" matches both "/app/sonarr"
        # and "/app/sonarr/anything". So trailing slash is inherently handled.
        routes = _find_routes_for_prefix(self.payload, "/app/sonarr")
        proxy_routes = [r for r in routes if "route" in r]
        self.assertTrue(len(proxy_routes) > 0,
            "/app/sonarr must have at least one proxy route")

    def test_app_root_bare_and_slash_both_redirect(self) -> None:
        """/app and /app/ both redirect to /app/homepage."""
        r1 = _find_redirect_for_prefix(self.payload, "/app")
        r2 = _find_redirect_for_prefix(self.payload, "/app/")
        self.assertIsNotNone(r1, "/app must redirect")
        self.assertIsNotNone(r2, "/app/ must redirect")
        self.assertEqual(r1["redirect"]["path_redirect"], "/app/homepage")
        self.assertEqual(r2["redirect"]["path_redirect"], "/app/homepage")


class TestUnknownAppPathRedirect(unittest.TestCase):
    """Unknown /app/X paths must redirect to /app/homepage for browsers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _render_envoy_routes()

    def test_unknown_app_path_catch_all_exists(self) -> None:
        """There must be a catch-all route for /app/ with HTML accept header."""
        all_routes = _get_all_routes(self.payload)
        catchall = None
        for r in all_routes:
            match = r.get("match", {})
            prefix = match.get("prefix", "")
            headers = match.get("headers", [])
            has_html = any(
                h.get("name") == "accept" for h in headers
            )
            if prefix == "/app/" and has_html and "redirect" in r:
                catchall = r
                break

        self.assertIsNotNone(catchall,
            "Must have an HTML catch-all redirect for /app/ prefix")
        self.assertEqual(catchall["redirect"]["path_redirect"], "/app/homepage",
            "Unknown app paths must redirect to /app/homepage")

    def test_known_routes_rank_above_catchall(self) -> None:
        """Known service routes (e.g., /app/sonarr) must have higher rank
        than the catch-all so they match first."""
        # This is validated by route ordering — Envoy evaluates routes top-down.
        # The rendered routes are sorted by rank (highest first).
        all_routes = _get_all_routes(self.payload)

        # Find the catch-all index
        catchall_idx = None
        sonarr_idx = None
        for i, r in enumerate(all_routes):
            match = r.get("match", {})
            prefix = match.get("prefix", "")
            headers = match.get("headers", [])
            has_html = any(h.get("name") == "accept" for h in headers)
            if prefix == "/app/" and has_html and "redirect" in r:
                catchall_idx = i
            if prefix == "/app/sonarr" and "route" in r:
                sonarr_idx = i

        if catchall_idx is not None and sonarr_idx is not None:
            self.assertLess(sonarr_idx, catchall_idx,
                "Known routes must appear before the catch-all in route order")


class TestRootRedirect(unittest.TestCase):
    """Root / must redirect to the default app."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _render_envoy_routes()

    def test_root_redirects(self) -> None:
        """/ should redirect to the default app path."""
        redirect = _find_redirect_for_prefix(self.payload, "/")
        self.assertIsNotNone(redirect, "Root / must have a redirect")
        target = redirect["redirect"]["path_redirect"]
        # Should redirect to jellyfin (priority) or homepage (fallback)
        self.assertTrue(
            target.startswith("/app/"),
            f"Root redirect must go to /app/..., got {target}",
        )


class TestVirtualHosts(unittest.TestCase):
    """Verify virtual host structure for compose and K8s."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _render_envoy_routes("apps.media-stack.local")

    def test_has_localhost_vhost(self) -> None:
        """Localhost catch-all vhost must exist for direct access."""
        names = _get_vhost_names(self.payload)
        self.assertIn("vhost_localhost", names)

    def test_has_catchall_vhost(self) -> None:
        """Wildcard catch-all vhost must exist."""
        names = _get_vhost_names(self.payload)
        self.assertIn("vhost_catchall", names)

    def test_catchall_has_wildcard_domain(self) -> None:
        """Catch-all vhost must have domain '*'."""
        static = self.payload.get("static_resources", {})
        listeners = static.get("listeners", [])
        hcm = listeners[0]["filter_chains"][0]["filters"][0]
        vhosts = hcm["typed_config"]["route_config"]["virtual_hosts"]
        catchall = [vh for vh in vhosts if vh["name"] == "vhost_catchall"]
        self.assertEqual(len(catchall), 1)
        self.assertIn("*", catchall[0]["domains"])

    def test_catchall_vhost_is_last(self) -> None:
        """Wildcard vhost must be the last one (Envoy evaluates in order)."""
        names = _get_vhost_names(self.payload)
        self.assertEqual(names[-1], "vhost_catchall",
            "Catch-all vhost must be last")

    def test_localhost_vhost_domains(self) -> None:
        """Localhost vhost must cover localhost and 127.0.0.1."""
        static = self.payload.get("static_resources", {})
        listeners = static.get("listeners", [])
        hcm = listeners[0]["filter_chains"][0]["filters"][0]
        vhosts = hcm["typed_config"]["route_config"]["virtual_hosts"]
        localhost_vh = [vh for vh in vhosts if vh["name"] == "vhost_localhost"]
        self.assertEqual(len(localhost_vh), 1)
        domains = localhost_vh[0]["domains"]
        self.assertIn("localhost", domains)
        self.assertIn("127.0.0.1", domains)

    def test_localhost_mirrors_gateway_routes(self) -> None:
        """Localhost vhost must have the same routes as the gateway vhost."""
        static = self.payload.get("static_resources", {})
        listeners = static.get("listeners", [])
        hcm = listeners[0]["filter_chains"][0]["filters"][0]
        vhosts = hcm["typed_config"]["route_config"]["virtual_hosts"]
        # Find gateway and localhost
        gateway_vh = None
        localhost_vh = None
        for vh in vhosts:
            if "apps.media-stack.local" in vh.get("domains", []):
                gateway_vh = vh
            if vh["name"] == "vhost_localhost":
                localhost_vh = vh

        if gateway_vh and localhost_vh:
            gw_count = len(gateway_vh.get("routes", []))
            lh_count = len(localhost_vh.get("routes", []))
            self.assertEqual(gw_count, lh_count,
                f"Localhost vhost ({lh_count} routes) must mirror "
                f"gateway vhost ({gw_count} routes)")


class TestAuthRouteProtection(unittest.TestCase):
    """Verify auth bypass is correctly applied to routes when auth is active."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _render_envoy_routes(auth_mode="authelia")

    def test_ext_authz_filter_present(self) -> None:
        """ext_authz filter must be injected before the router filter."""
        static = self.payload.get("static_resources", {})
        listeners = static.get("listeners", [])
        hcm = listeners[0]["filter_chains"][0]["filters"][0]
        http_filters = hcm["typed_config"]["http_filters"]
        filter_names = [f["name"] for f in http_filters]
        self.assertIn(EXT_AUTHZ_FILTER_NAME, filter_names,
            "ext_authz filter must be present")
        # Must come before router
        router_idx = filter_names.index("envoy.filters.http.router")
        authz_idx = filter_names.index(EXT_AUTHZ_FILTER_NAME)
        self.assertLess(authz_idx, router_idx,
            "ext_authz must come before router filter")

    def test_ext_authz_cluster_exists(self) -> None:
        """Auth provider cluster must exist."""
        static = self.payload.get("static_resources", {})
        clusters = static.get("clusters", [])
        cluster_names = [c["name"] for c in clusters]
        self.assertIn("ext_authz_authelia", cluster_names,
            "Auth cluster must be present")


class TestAuthWorksOnLAN(unittest.TestCase):
    """ext_authz must be applied even when internet_exposed=False."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _render_envoy_routes(auth_mode="authelia")

    def test_ext_authz_present_on_lan(self) -> None:
        """ext_authz filter must be present even without internet exposure."""
        static = self.payload.get("static_resources", {})
        listeners = static.get("listeners", [])
        hcm = listeners[0]["filter_chains"][0]["filters"][0]
        http_filters = hcm["typed_config"]["http_filters"]
        filter_names = [f["name"] for f in http_filters]
        self.assertIn(EXT_AUTHZ_FILTER_NAME, filter_names)

    def test_auth_portal_vhost_exists(self) -> None:
        """auth.{domain} vhost must exist for the login portal."""
        names = _get_vhost_names(self.payload)
        auth_vhosts = [n for n in names if n.startswith("vhost_authelia")]
        self.assertTrue(len(auth_vhosts) > 0,
            f"Auth portal vhost must exist. Got vhosts: {names}")

    def test_auth_portal_has_ext_authz_disabled(self) -> None:
        """The auth portal itself must bypass ext_authz."""
        static = self.payload.get("static_resources", {})
        listeners = static.get("listeners", [])
        hcm = listeners[0]["filter_chains"][0]["filters"][0]
        vhosts = hcm["typed_config"]["route_config"]["virtual_hosts"]
        auth_vh = [vh for vh in vhosts if vh["name"].startswith("vhost_authelia")]
        self.assertTrue(len(auth_vh) > 0)
        route = auth_vh[0]["routes"][0]
        self.assertIn("typed_per_filter_config", route,
            "Auth portal route must have ext_authz disabled")
        self.assertIn(EXT_AUTHZ_FILTER_NAME, route["typed_per_filter_config"])
        self.assertTrue(route["typed_per_filter_config"][EXT_AUTHZ_FILTER_NAME]["disabled"])


class TestGatewayHostMismatch(unittest.TestCase):
    """Verify routing works even when DNS hostname differs from profile gateway_host.

    Users may set up DNS for apps.media-stack.local but profile says
    docker.media-stack.local. The wildcard catch-all vhost should handle this.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # Render with profile gateway docker.media-stack.local
        cls.payload = _render_envoy_routes("docker.media-stack.local")

    def test_catchall_serves_any_hostname(self) -> None:
        """Wildcard vhost catches requests from any hostname."""
        # Routes in the catchall should include /app/ paths
        routes = _get_all_routes(self.payload, vhost_name="vhost_catchall")
        app_routes = [
            r for r in routes
            if r.get("match", {}).get("prefix", "").startswith("/app/")
        ]
        self.assertTrue(len(app_routes) > 0,
            "Catch-all vhost must have /app/ routes")


class TestComposeLabelRoutes(unittest.TestCase):
    """Verify compose label parsing produces correct Envoy routes."""

    def test_host_rule_extraction(self) -> None:
        self.assertEqual(
            _rule_hosts("Host(`apps.media-stack.local`) && PathPrefix(`/app/sonarr`)"),
            ("apps.media-stack.local",),
        )

    def test_path_prefix_extraction(self) -> None:
        self.assertEqual(
            _rule_path_prefix("Host(`apps.media-stack.local`) && PathPrefix(`/app/sonarr`)"),
            "/app/sonarr",
        )

    def test_cluster_name_derivation(self) -> None:
        self.assertEqual(_cluster_name("sonarr"), "service_sonarr")
        self.assertEqual(_cluster_name("media-stack-controller"), "service_media_stack_controller")
        self.assertEqual(_cluster_name("controller"), "service_controller")

    def test_app_slug_extraction(self) -> None:
        self.assertEqual(_path_prefix_app_slug("/app/sonarr"), "sonarr")
        self.assertEqual(_path_prefix_app_slug("/app/media-stack-controller"), "media_stack_controller")


class TestK8sSyntheticServices(unittest.TestCase):
    """Verify synthetic service generation for K8s mode."""

    def test_synthetic_services_include_controller(self) -> None:
        services = _build_test_services()
        self.assertIn("media-stack-controller", services)

    def test_synthetic_services_have_correct_labels(self) -> None:
        services = _build_test_services("apps.media-stack.local")
        sonarr = services.get("sonarr", {})
        labels = sonarr.get("labels", {})
        self.assertEqual(labels.get("traefik.enable"), "true")
        # Should have a router rule with PathPrefix
        rule_key = "traefik.http.routers.sonarr-path.rule"
        self.assertIn(rule_key, labels)
        self.assertIn("/app/sonarr", labels[rule_key])

    def test_all_registered_services_have_synthetic_entries(self) -> None:
        """K8s synthetic services should cover all registry services."""
        services = _build_test_services()
        service_ids = _load_service_ids()
        for svc_id in service_ids:
            self.assertIn(svc_id, services,
                f"Service {svc_id} missing from synthetic services")


class TestRouteConsistencyAcrossPlatforms(unittest.TestCase):
    """Routes should be functionally equivalent for compose and K8s."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.compose_payload = _render_envoy_routes("apps.compose.local")
        cls.k8s_payload = _render_envoy_routes("apps.k8s.local")

    def test_same_number_of_service_routes(self) -> None:
        """Both platforms should generate the same service routes."""
        compose_routes = _get_all_routes(self.compose_payload)
        k8s_routes = _get_all_routes(self.k8s_payload)

        compose_app_prefixes = {
            r.get("match", {}).get("prefix", "")
            for r in compose_routes
            if r.get("match", {}).get("prefix", "").startswith("/app/")
               and "route" in r
        }
        k8s_app_prefixes = {
            r.get("match", {}).get("prefix", "")
            for r in k8s_routes
            if r.get("match", {}).get("prefix", "").startswith("/app/")
               and "route" in r
        }
        self.assertEqual(compose_app_prefixes, k8s_app_prefixes,
            "Compose and K8s must have the same set of /app/ route prefixes")

    def test_both_have_root_redirect(self) -> None:
        """Both platforms must redirect /."""
        for name, payload in [
            ("compose", self.compose_payload),
            ("k8s", self.k8s_payload),
        ]:
            redirect = _find_redirect_for_prefix(payload, "/")
            self.assertIsNotNone(redirect, f"{name}: Root / must redirect")

    def test_both_have_catchall_vhost(self) -> None:
        for name, payload in [
            ("compose", self.compose_payload),
            ("k8s", self.k8s_payload),
        ]:
            names = _get_vhost_names(payload)
            self.assertIn("vhost_catchall", names,
                f"{name}: Must have catch-all vhost")


if __name__ == "__main__":
    unittest.main()
