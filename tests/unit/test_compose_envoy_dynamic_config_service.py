import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.compose.edge.providers.envoy.dynamic_config import (  # noqa: E402
    EnvoyDynamicConfigService,
)


class EnvoyDynamicConfigServiceTests(unittest.TestCase):
    @staticmethod
    def _traefik_payload() -> dict:
        return {
            "http": {
                "routers": {
                    "homepage-path": {
                        "rule": "Host(`apps.media-dev.local`) && PathPrefix(`/app/homepage`)",
                        "service": "homepage",
                        "middlewares": ["homepage-stripprefix"],
                    }
                },
                "middlewares": {
                    "homepage-stripprefix": {
                        "stripPrefix": {"prefixes": ["/app/homepage"]},
                    }
                },
                "services": {
                    "homepage": {
                        "loadBalancer": {
                            "servers": [{"url": "http://homepage:3000"}],
                        }
                    }
                },
            }
        }

    @staticmethod
    def _template_payload() -> dict:
        return {
            "static_resources": {
                "listeners": [
                    {
                        "name": "listener_http",
                        "filter_chains": [
                            {
                                "filters": [
                                    {
                                        "name": "envoy.filters.network.http_connection_manager",
                                        "typed_config": {
                                            "route_config": {
                                                "name": "media_stack_routes",
                                                "virtual_hosts": [],
                                            },
                                            "http_filters": [
                                                {"name": "envoy.filters.http.lua"},
                                                {"name": "envoy.filters.http.router"},
                                            ],
                                        },
                                    }
                                ]
                            }
                        ],
                    }
                ],
                "clusters": [],
            },
            "admin": {"access_log_path": "/dev/null"},
        }

    def _service(
        self,
        *,
        template_loader=None,
        compose_env: dict[str, str] | None = None,
        runtime_template_path: Path | None = None,
    ):
        route_graph = mock.Mock()
        route_graph.render.return_value = SimpleNamespace(payload=self._traefik_payload())
        spec_resolver = mock.Mock()
        spec_resolver.compose_environment.return_value = dict(compose_env or {})
        return EnvoyDynamicConfigService(
            route_graph_service=route_graph,
            spec_resolver=spec_resolver,
            template_loader=template_loader,
            runtime_template_path=runtime_template_path,
        )

    def test_render_injects_routes_and_clusters_from_template(self):
        template = self._template_payload()

        def loader(_template_path: Path) -> dict:
            return template

        service = self._service(template_loader=loader)
        rendered = service.render(services={})
        http_filters = (
            (
                (
                    ((rendered.payload.get("static_resources") or {}).get("listeners") or [{}])[
                        0
                    ].get("filter_chains")
                    or [{}]
                )[0].get("filters")
                or [{}]
            )[0]
            .get("typed_config", {})
            .get("http_filters", [])
        )
        filter_names = [str(item.get("name") or "") for item in http_filters]
        self.assertIn("envoy.filters.http.lua", filter_names)

        virtual_hosts = (
            (
                (
                    ((rendered.payload.get("static_resources") or {}).get("listeners") or [{}])[
                        0
                    ].get("filter_chains")
                    or [{}]
                )[0].get("filters")
                or [{}]
            )[0]
            .get("typed_config", {})
            .get("route_config", {})
            .get("virtual_hosts", [])
        )
        self.assertEqual(len(virtual_hosts), 1)
        self.assertEqual((virtual_hosts[0].get("domains") or [None])[0], "apps.media-dev.local")
        routes = virtual_hosts[0].get("routes") or []
        self.assertGreaterEqual(len(routes), 2)
        primary_route = routes[0]
        fallback_route = routes[1]
        self.assertEqual((primary_route.get("match") or {}).get("prefix"), "/app/homepage")
        self.assertEqual(
            (
                ((primary_route.get("request_headers_to_add") or [{}])[0].get("header") or {}).get(
                    "key"
                )
            ),
            "x-forwarded-prefix",
        )
        self.assertEqual(
            (
                ((primary_route.get("response_headers_to_add") or [{}])[0].get("header") or {}).get(
                    "key"
                )
            ),
            "x-media-stack-prefix",
        )
        response_header_keys = [
            str((entry.get("header") or {}).get("key") or "")
            for entry in (primary_route.get("response_headers_to_add") or [])
            if isinstance(entry, dict)
        ]
        self.assertIn("set-cookie", response_header_keys)
        self.assertEqual((fallback_route.get("match") or {}).get("prefix"), "/")
        fallback_headers = (fallback_route.get("match") or {}).get("headers") or []
        self.assertTrue(bool(fallback_headers))
        self.assertIn(
            "/app/homepage",
            str((fallback_headers[0].get("safe_regex_match") or {}).get("regex") or ""),
        )
        cookie_routes = [
            route
            for route in routes
            if any(
                str(header.get("name") or "") == "cookie"
                for header in ((route.get("match") or {}).get("headers") or [])
                if isinstance(header, dict)
            )
        ]
        self.assertTrue(cookie_routes)
        cookie_regex = str(
            (
                (
                    (
                        (cookie_routes[0].get("match") or {}).get("headers") or [{}]
                    )[0].get("safe_regex_match")
                    or {}
                ).get("regex")
                or ""
            )
        )
        self.assertIn("media_stack_app=homepage", cookie_regex)
        html_redirect_routes = [
            route
            for route in routes
            if "redirect" in route
            and any(
                str(header.get("name") or "") == "accept"
                for header in ((route.get("match") or {}).get("headers") or [])
                if isinstance(header, dict)
            )
        ]
        self.assertTrue(html_redirect_routes)
        self.assertEqual(
            str((html_redirect_routes[0].get("redirect") or {}).get("path_redirect") or ""),
            "/app/homepage",
        )

        clusters = (rendered.payload.get("static_resources") or {}).get("clusters") or []
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].get("name"), "service_homepage")
        self.assertEqual(
            (
                (
                    ((clusters[0].get("load_assignment") or {}).get("endpoints") or [{}])[0].get(
                        "lb_endpoints"
                    )
                    or [{}]
                )[0]
                .get("endpoint", {})
                .get("address", {})
                .get("socket_address", {})
                .get("address")
            ),
            "homepage",
        )

        self.assertEqual(
            (
                template["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
                    "typed_config"
                ]["route_config"]["virtual_hosts"]
            ),
            [],
        )

    def test_render_raises_for_invalid_template_shape(self):
        service = self._service(template_loader=lambda _path: {"static_resources": {}})
        with self.assertRaisesRegex(RuntimeError, "listener"):
            service.render(services={})

    def test_render_raises_when_template_file_missing(self):
        service = self._service(
            compose_env={"ENVOY_RUNTIME_TEMPLATE_FILE": "/tmp/does-not-exist/envoy.yaml"}
        )
        with self.assertRaisesRegex(RuntimeError, "not found"):
            service.render(services={})


if __name__ == "__main__":
    unittest.main()
