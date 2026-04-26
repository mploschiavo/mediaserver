import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.platforms.compose.edge.provider_contract import (  # noqa: E402
    ComposeEdgeProviderRuntimeContext,
)
from media_stack.core.platforms.compose.edge.provider_registry import (  # noqa: E402
    build_compose_edge_runtime_patchers,
    load_compose_edge_provider_plugins,
)
from media_stack.core.platforms.compose.services.labels import (  # noqa: E402
    ComposeLabelConfig,
    ComposeLabelService,
)


_TRAEFIK_SPEC = {
    "enable_label_key": "traefik.enable",
    "router_label_prefix": "traefik.http.routers.",
    "service_label_prefix": "traefik.http.services.",
    "middleware_label_prefix": "traefik.http.middlewares.",
    "router_rule_key_template": "traefik.http.routers.{router_name}.rule",
    "router_service_key_template": "traefik.http.routers.{router_name}.service",
    "router_middleware_key_template": "traefik.http.routers.{router_name}.middlewares",
    "strip_prefix_key_template": "traefik.http.middlewares.{middleware_name}.stripprefix.prefixes",
    "path_rule_template": "Host(`{gateway_host}`) && PathPrefix(`{path_prefix}`)",
}


class ComposeEdgeProviderRegistryTests(unittest.TestCase):
    def test_builtin_plugins_include_traefik_and_envoy(self):
        plugins = load_compose_edge_provider_plugins()
        self.assertIn("traefik", plugins)
        self.assertIn("envoy", plugins)

    def test_build_runtime_patchers_returns_callable_patchers(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root = Path(tmp) / "config"
            label_service = ComposeLabelService(
                cfg=ComposeLabelConfig(
                    project_name="media-dev",
                    edge_router_provider="traefik",
                    edge_compose_provider_specs={
                        "traefik": dict(_TRAEFIK_SPEC),
                        "envoy": dict(_TRAEFIK_SPEC),
                    },
                )
            )
            spec_resolver = mock.Mock()
            spec_resolver.config_root.return_value = config_root
            spec_resolver.compose_environment.return_value = {}
            spec_resolver.container_name.side_effect = (
                lambda service_name, _spec: f"{service_name}-container"
            )
            route_graph_service = mock.Mock()
            route_graph_service.render.return_value = mock.Mock(
                payload={"http": {"routers": {}, "services": {}, "middlewares": {}}},
                router_count=0,
                service_count=0,
                middleware_count=0,
            )
            artifacts_service = mock.Mock()
            info = mock.Mock()

            patchers = build_compose_edge_runtime_patchers(
                ComposeEdgeProviderRuntimeContext(
                    label_service=label_service,
                    spec_resolver=spec_resolver,
                    route_graph_service=route_graph_service,
                    artifacts_service=artifacts_service,
                    info=info,
                )
            )

            self.assertIn("traefik", patchers)
            self.assertIn("envoy", patchers)

            traefik_result = patchers["traefik"]({})
            self.assertEqual(traefik_result.provider, "traefik")
            self.assertTrue(traefik_result.applied)

            envoy_result = patchers["envoy"]({})
            self.assertEqual(envoy_result.provider, "envoy")
            self.assertTrue(envoy_result.applied)


if __name__ == "__main__":
    unittest.main()
