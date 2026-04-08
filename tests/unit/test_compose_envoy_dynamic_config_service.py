import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.platforms.compose.edge.providers.envoy.dynamic_config import (  # noqa: E402
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
    def _multi_app_payload() -> dict:
        return {
            "http": {
                "routers": {
                    "maintainerr-path": {
                        "rule": "Host(`apps.media-dev.local`) && PathPrefix(`/app/maintainerr`)",
                        "service": "maintainerr",
                        "middlewares": ["maintainerr-stripprefix"],
                    },
                    "homepage-path": {
                        "rule": "Host(`apps.media-dev.local`) && PathPrefix(`/app/homepage`)",
                        "service": "homepage",
                        "middlewares": ["homepage-stripprefix"],
                    },
                },
                "middlewares": {
                    "maintainerr-stripprefix": {
                        "stripPrefix": {"prefixes": ["/app/maintainerr"]},
                    },
                    "homepage-stripprefix": {
                        "stripPrefix": {"prefixes": ["/app/homepage"]},
                    },
                },
                "services": {
                    "maintainerr": {
                        "loadBalancer": {
                            "servers": [{"url": "http://maintainerr:6246"}],
                        }
                    },
                    "homepage": {
                        "loadBalancer": {
                            "servers": [{"url": "http://homepage:3000"}],
                        }
                    },
                },
            }
        }

    @staticmethod
    def _path_prefix_passthrough_payload() -> dict:
        return {
            "http": {
                "routers": {
                    "sonarr-path": {
                        "rule": "Host(`apps.media-dev.local`) && PathPrefix(`/app/sonarr`)",
                        "service": "sonarr",
                    }
                },
                "middlewares": {},
                "services": {
                    "sonarr": {
                        "loadBalancer": {
                            "servers": [{"url": "http://sonarr:8989"}],
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
        self.assertEqual(len(virtual_hosts), 2)
        self.assertEqual((virtual_hosts[0].get("domains") or [None])[0], "apps.media-dev.local")
        self.assertEqual(virtual_hosts[1].get("name"), "vhost_localhost")
        routes = virtual_hosts[0].get("routes") or []
        self.assertGreaterEqual(len(routes), 3)
        html_primary_route = next(
            (
                route
                for route in routes
                if (route.get("match") or {}).get("prefix") == "/app/homepage"
                and any(
                    str(header.get("name") or "") == "accept"
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            None,
        )
        self.assertIsNotNone(html_primary_route)
        html_request_headers = html_primary_route.get("request_headers_to_add") or []
        self.assertIn(
            {
                "header": {
                    "key": "accept-encoding",
                    "value": "identity",
                },
                "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
            },
            html_request_headers,
            "HTML document routes must disable upstream compression so the Lua prefix patch can rewrite the response body.",
        )
        primary_route = next(
            (
                route
                for route in routes
                if (route.get("match") or {}).get("prefix") == "/app/homepage"
                and not any(
                    str(header.get("name") or "") == "accept"
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            None,
        )
        self.assertIsNotNone(primary_route)
        fallback_route = next(
            (
                route
                for route in routes
                if (route.get("match") or {}).get("prefix") == "/"
                and "route" in route
                and any(
                    str(header.get("name") or "") == "referer"
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            None,
        )
        self.assertIsNotNone(fallback_route)
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
        html_response_header_keys = [
            str((entry.get("header") or {}).get("key") or "")
            for entry in (html_primary_route.get("response_headers_to_add") or [])
            if isinstance(entry, dict)
        ]
        self.assertNotIn("set-cookie", response_header_keys)
        self.assertIn("set-cookie", html_response_header_keys)
        fallback_headers = (fallback_route.get("match") or {}).get("headers") or []
        self.assertTrue(bool(fallback_headers))
        self.assertIn(
            "/app/homepage",
            str((fallback_headers[0].get("safe_regex_match") or {}).get("regex") or ""),
        )
        fallback_rewrite = (fallback_route.get("route") or {}).get("regex_rewrite") or {}
        self.assertEqual(
            str(((fallback_rewrite.get("pattern") or {}).get("regex") or "")),
            r"^/app/?(.*)$",
        )
        cookie_routes = [
            route
            for route in routes
            if "route" in route
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
                    ((cookie_routes[0].get("match") or {}).get("headers") or [{}])[0].get(
                        "safe_regex_match"
                    )
                    or {}
                ).get("regex")
                or ""
            )
        )
        self.assertIn("media_stack_app_homepage=1", cookie_regex)
        self.assertTrue(cookie_regex.startswith(".*"))
        self.assertTrue(cookie_regex.endswith(".*"))
        cookie_rewrite = (cookie_routes[0].get("route") or {}).get("regex_rewrite") or {}
        self.assertEqual(
            str(((cookie_rewrite.get("pattern") or {}).get("regex") or "")),
            r"^/app/?(.*)$",
        )
        referer_proxy_routes = [
            route
            for route in routes
            if "route" in route
            and any(
                str(header.get("name") or "") == "referer"
                for header in ((route.get("match") or {}).get("headers") or [])
                if isinstance(header, dict)
            )
        ]
        self.assertTrue(referer_proxy_routes)
        html_redirect_routes = [
            route
            for route in routes
            if "redirect" in route
            and (route.get("redirect") or {}).get("path_redirect")
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

    def test_referer_fallback_routes_have_precedence_over_cookie_routes(self):
        template = self._template_payload()
        service = self._service(template_loader=lambda _path: template)
        service.route_graph_service.render.return_value = SimpleNamespace(
            payload=self._multi_app_payload()
        )
        rendered = service.render(services={})
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
        self.assertTrue(virtual_hosts)
        routes = virtual_hosts[0].get("routes") or []
        referer_homepage_proxy_idx = next(
            (
                idx
                for idx, route in enumerate(routes)
                if "route" in route
                if any(
                    str(header.get("name") or "") == "referer"
                    and "/app/homepage"
                    in str((header.get("safe_regex_match") or {}).get("regex") or "")
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            -1,
        )
        cookie_homepage_proxy_idx = next(
            (
                idx
                for idx, route in enumerate(routes)
                if "route" in route
                if any(
                    str(header.get("name") or "") == "cookie"
                    and "media_stack_app_homepage=1"
                    in str((header.get("safe_regex_match") or {}).get("regex") or "")
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            -1,
        )
        self.assertGreaterEqual(referer_homepage_proxy_idx, 0)
        self.assertGreaterEqual(cookie_homepage_proxy_idx, 0)
        self.assertLess(
            referer_homepage_proxy_idx,
            cookie_homepage_proxy_idx,
            "Referer fallback routes must be evaluated before cookie routes for the same app.",
        )

    def test_render_raises_when_template_file_missing(self):
        service = self._service(
            compose_env={"ENVOY_RUNTIME_TEMPLATE_FILE": "/tmp/does-not-exist/envoy.yaml"}
        )
        with self.assertRaisesRegex(RuntimeError, "not found"):
            service.render(services={})

    @staticmethod
    def _jellyseerr_path_prefix_payload() -> dict:
        return {
            "http": {
                "routers": {
                    "jellyseerr-path": {
                        "rule": "Host(`apps.media-dev.local`) && PathPrefix(`/app/jellyseerr`)",
                        "service": "jellyseerr",
                        "middlewares": ["jellyseerr-stripprefix"],
                    }
                },
                "middlewares": {
                    "jellyseerr-stripprefix": {
                        "stripPrefix": {
                            "prefixes": ["/app/jellyseerr"],
                        }
                    }
                },
                "services": {
                    "jellyseerr": {
                        "loadBalancer": {
                            "servers": [{"url": "http://jellyseerr:5055"}],
                        }
                    }
                },
            }
        }

    def test_render_rewrites_path_prefix_for_jellyseerr(self):
        template = self._template_payload()
        service = self._service(template_loader=lambda _path: template)
        service.route_graph_service.render.return_value = SimpleNamespace(
            payload=self._jellyseerr_path_prefix_payload()
        )
        rendered = service.render(services={})
        virtual_hosts = (
            (
                (
                    (
                        (rendered.payload.get("static_resources") or {}).get("listeners") or [{}]
                    )[0].get("filter_chains")
                    or [{}]
                )[0].get("filters")
                or [{}]
            )[0]
            .get("typed_config", {})
            .get("route_config", {})
            .get("virtual_hosts", [])
        )
        self.assertTrue(virtual_hosts)
        routes = virtual_hosts[0].get("routes") or []
        primary_route = next(
            (
                route
                for route in routes
                if (route.get("match") or {}).get("prefix") == "/app/jellyseerr"
            ),
            None,
        )
        self.assertIsNotNone(primary_route, "Primary route for /app/jellyseerr must exist")
        html_primary_route = next(
            (
                route
                for route in routes
                if (route.get("match") or {}).get("prefix") == "/app/jellyseerr"
                and any(
                    str(header.get("name") or "") == "accept"
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            None,
        )
        self.assertIsNotNone(
            html_primary_route,
            "HTML primary route for /app/jellyseerr must exist",
        )
        self.assertIn(
            {
                "header": {
                    "key": "accept-encoding",
                    "value": "identity",
                },
                "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
            },
            html_primary_route.get("request_headers_to_add") or [],
            "Jellyseerr HTML document routes must disable upstream compression so the Lua prefix patch reaches the browser.",
        )
        self.assertEqual(
            (primary_route.get("route") or {}).get("regex_rewrite"),
            {
                "pattern": {
                    "google_re2": {},
                    "regex": "^/app/jellyseerr/?(.*)$",
                },
                "substitution": r"/\1",
            },
            "Jellyseerr must strip /app/jellyseerr before proxying to the upstream app",
        )
        cookie_route = next(
            (
                route
                for route in routes
                if "route" in route
                if any(
                    str(header.get("name") or "") == "cookie"
                    and "media_stack_app_jellyseerr=1"
                    in str((header.get("safe_regex_match") or {}).get("regex") or "")
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            None,
        )
        self.assertIsNotNone(cookie_route, "Jellyseerr must include cookie fallback routing")
        self.assertEqual(
            (cookie_route.get("route") or {}).get("regex_rewrite"),
            {
                "pattern": {
                    "google_re2": {},
                    "regex": "^/app/?(.*)$",
                },
                "substitution": r"/\1",
            },
            "Jellyseerr root-relative follow-up requests must be rewritten back to upstream root paths",
        )
        cookie_rewrite = (cookie_route.get("route") or {}).get("regex_rewrite") or {}
        self.assertEqual(
            str(((cookie_rewrite.get("pattern") or {}).get("regex") or "")),
            r"^/app/?(.*)$",
        )
        self.assertEqual(
            str(cookie_rewrite.get("substitution") or ""),
            r"/\1",
        )
        cookie_html_redirect_route = next(
            (
                route
                for route in routes
                if "redirect" in route
                and any(
                    str(header.get("name") or "") == "cookie"
                    and "media_stack_app_jellyseerr=1"
                    in str((header.get("safe_regex_match") or {}).get("regex") or "")
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
                and any(
                    str(header.get("name") or "") == "accept"
                    for header in ((route.get("match") or {}).get("headers") or [])
                    if isinstance(header, dict)
                )
            ),
            None,
        )
        if cookie_html_redirect_route is not None:
            cookie_html_redirect_rewrite = (
                (cookie_html_redirect_route.get("redirect") or {}).get("regex_rewrite") or {}
            )
            self.assertEqual(
                str(((cookie_html_redirect_rewrite.get("pattern") or {}).get("regex") or "")),
                r"^/(.*)$",
            )
            self.assertEqual(
                str(cookie_html_redirect_rewrite.get("substitution") or ""),
                r"/app/jellyseerr/\1",
            )
        else:
            html_redirect = next(
                (
                    route
                    for route in routes
                    if "redirect" in route
                    and str((route.get("redirect") or {}).get("path_redirect") or "")
                    == "/app/jellyseerr"
                ),
                None,
            )
            self.assertIsNotNone(html_redirect)

    def test_render_does_not_rewrite_path_prefix_when_strip_middleware_absent(self):
        template = self._template_payload()
        service = self._service(template_loader=lambda _path: template)
        service.route_graph_service.render.return_value = SimpleNamespace(
            payload=self._path_prefix_passthrough_payload()
        )
        rendered = service.render(services={})
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
        self.assertTrue(virtual_hosts)
        routes = virtual_hosts[0].get("routes") or []
        primary_route = next(
            (
                route
                for route in routes
                if (route.get("match") or {}).get("prefix") == "/app/sonarr"
            ),
            None,
        )
        self.assertIsNotNone(primary_route)
        self.assertNotIn("regex_rewrite", (primary_route.get("route") or {}))


if __name__ == "__main__":
    unittest.main()
