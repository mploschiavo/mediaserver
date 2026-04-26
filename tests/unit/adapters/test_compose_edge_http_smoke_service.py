import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.platforms.compose.services.edge_http_smoke import (  # noqa: E402
    ComposeEdgeHttpResponse,
    ComposeEdgeHttpSmokeService,
)


class _RenderResult:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


def _route_rule(*, host: str, path_prefix: str) -> str:
    return f"Host(`{host}`) && PathPrefix(`{path_prefix}`)"


class ComposeEdgeHttpSmokeServiceTests(unittest.TestCase):
    def _service(
        self,
        *,
        route_strategy: str,
        gateway_host: str = "apps.media-dev.local",
        gateway_port: int = 18080,
        app_path_prefix: str = "/app",
        routers: dict[str, dict] | None = None,
        compose_env: dict[str, str] | None = None,
        http_get=None,
    ) -> ComposeEdgeHttpSmokeService:
        label_service = mock.Mock()
        label_service.route_strategy.return_value = route_strategy
        label_service.cfg = SimpleNamespace(
            app_gateway_host=gateway_host,
            app_path_prefix=app_path_prefix,
        )

        spec_resolver = mock.Mock()
        merged_env = {"APP_GATEWAY_PORT": str(gateway_port)}
        merged_env.update(dict(compose_env or {}))
        spec_resolver.compose_environment.return_value = merged_env

        dynamic_config_service = mock.Mock()
        dynamic_config_service.render.return_value = _RenderResult(
            {
                "http": {
                    "routers": dict(routers or {}),
                }
            }
        )

        return ComposeEdgeHttpSmokeService(
            label_service=label_service,
            spec_resolver=spec_resolver,
            route_graph_service=dynamic_config_service,
            info=mock.Mock(),
            http_get=http_get,
        )

    def test_run_skips_for_subdomain_strategy(self):
        http_get = mock.Mock()
        service = self._service(
            route_strategy="subdomain",
            routers={
                "app": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/homepage")
                }
            },
            http_get=http_get,
        )

        service.run(services={})

        http_get.assert_not_called()

    def test_run_passes_when_route_and_assets_load(self):
        requests: list[tuple[str, int, str, dict[str, str]]] = []

        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            requests.append((host, port, path, dict(headers)))
            responses = {
                ("apps.media-dev.local", 18080, "/app/homepage"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=(
                        '<html><head><link rel="stylesheet" '
                        'href="/app/homepage/assets/main.css"></head></html>'
                    ),
                ),
                ("apps.media-dev.local", 18080, "/app/homepage/assets/main.css"): (
                    ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "text/css"},
                        body="body{}",
                    )
                ),
                ("apps.media-dev.local", 18080, "/app/homepage/api/services"): (
                    ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "application/json"},
                        body=(
                            '[{"name":"Media Stack","services":['
                            '{"name":"Homepage","href":"http://apps.media-dev.local:18080/app/homepage"}'
                            "]}]"
                        ),
                    )
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "app": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/homepage")
                }
            },
            http_get=http_get,
        )

        service.run(services={})

        asset_calls = [item for item in requests if item[2] == "/app/homepage/assets/main.css"]
        self.assertGreaterEqual(len(asset_calls), 1)
        self.assertTrue(
            all(
                item[3].get("Referer") == "http://apps.media-dev.local:18080/app/homepage"
                for item in asset_calls
            )
        )

    def test_run_fails_when_redirect_escapes_route_prefix(self):
        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            responses = {
                ("apps.media-dev.local", 18080, "/app/sonarr"): ComposeEdgeHttpResponse(
                    status=302,
                    headers={"Location": "/login"},
                    body="",
                ),
                ("apps.media-dev.local", 18080, "/login"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body="<html>login</html>",
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "sonarr": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/sonarr")
                }
            },
            http_get=http_get,
        )

        with self.assertRaisesRegex(RuntimeError, "redirect/path escape"):
            service.run(services={})

    def test_run_fails_when_route_lands_on_wizard_page(self):
        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            responses = {
                ("apps.media-dev.local", 18080, "/app/sabnzbd"): ComposeEdgeHttpResponse(
                    status=303,
                    headers={"Location": "/app/sabnzbd/wizard/"},
                    body="",
                ),
                ("apps.media-dev.local", 18080, "/app/sabnzbd/wizard/"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body="<html>wizard</html>",
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "sabnzbd": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/sabnzbd")
                }
            },
            http_get=http_get,
        )

        with self.assertRaisesRegex(RuntimeError, "wizard path"):
            service.run(services={})

    def test_run_fails_when_page_returns_200_but_asset_is_404(self):
        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            responses = {
                ("apps.media-dev.local", 18080, "/app/homepage"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body='<html><body><script src="/assets/app.js"></script></body></html>',
                ),
                ("apps.media-dev.local", 18080, "/assets/app.js"): ComposeEdgeHttpResponse(
                    status=404,
                    headers={"Content-Type": "text/plain"},
                    body="missing",
                ),
                ("apps.media-dev.local", 18080, "/app/homepage/api/services"): (
                    ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "application/json"},
                        body=(
                            '[{"name":"Media Stack","services":['
                            '{"name":"Homepage","href":"http://apps.media-dev.local:18080/app/homepage"}'
                            "]}]"
                        ),
                    )
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "homepage": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/homepage")
                }
            },
            http_get=http_get,
        )

        with self.assertRaisesRegex(RuntimeError, "asset"):
            service.run(services={})

    def test_run_allows_optional_homepage_icon_404(self):
        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            responses = {
                ("apps.media-dev.local", 18080, "/app/homepage"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body=(
                        '<html><head><link rel="apple-touch-icon" '
                        'href="/apple-touch-icon.png?v=4"></head></html>'
                    ),
                ),
                (
                    "apps.media-dev.local",
                    18080,
                    "/apple-touch-icon.png?v=4",
                ): ComposeEdgeHttpResponse(
                    status=404,
                    headers={"Content-Type": "text/plain"},
                    body="missing",
                ),
                ("apps.media-dev.local", 18080, "/app/homepage/api/services"): (
                    ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "application/json"},
                        body=(
                            '[{"name":"Media Stack","services":['
                            '{"name":"Homepage","href":"http://apps.media-dev.local:18080/app/homepage"}'
                            "]}]"
                        ),
                    )
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "homepage": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/homepage")
                }
            },
            http_get=http_get,
        )

        service.run(services={})

    def test_run_fails_when_homepage_tile_link_targets_404(self):
        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            responses = {
                ("apps.media-dev.local", 18080, "/app/homepage"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body="<html>ok</html>",
                ),
                ("apps.media-dev.local", 18080, "/app/homepage/api/services"): (
                    ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "application/json"},
                        body=(
                            '[{"name":"Media Stack","services":['
                            '{"name":"Sonarr","href":"http://apps.media-dev.local:18080/app/sonarr"}'
                            "]}]"
                        ),
                    )
                ),
                ("apps.media-dev.local", 18080, "/app/sonarr"): ComposeEdgeHttpResponse(
                    status=404,
                    headers={"Content-Type": "text/plain"},
                    body="missing",
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "homepage": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/homepage")
                },
            },
            http_get=http_get,
        )

        with self.assertRaisesRegex(RuntimeError, "final status 404"):
            service.run(services={})

    def test_run_fails_when_homepage_tile_link_targets_unauthorized_route(self):
        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            responses = {
                ("apps.media-dev.local", 18080, "/app/homepage"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body="<html>ok</html>",
                ),
                ("apps.media-dev.local", 18080, "/app/homepage/api/services"): (
                    ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "application/json"},
                        body=(
                            '[{"name":"Media Stack","services":['
                            '{"name":"qBittorrent","href":"http://apps.media-dev.local:18080/app/qbittorrent"}'
                            "]}]"
                        ),
                    )
                ),
                ("apps.media-dev.local", 18080, "/app/qbittorrent"): ComposeEdgeHttpResponse(
                    status=401,
                    headers={"Content-Type": "text/plain"},
                    body="unauthorized",
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "homepage": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/homepage")
                },
            },
            http_get=http_get,
        )

        with self.assertRaisesRegex(RuntimeError, "final status 401"):
            service.run(services={})

    def test_run_fails_when_homepage_services_api_has_no_gateway_tile_links(self):
        def http_get(host: str, port: int, path: str, headers: dict[str, str]):
            responses = {
                ("apps.media-dev.local", 18080, "/app/homepage"): ComposeEdgeHttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body="<html>ok</html>",
                ),
                ("apps.media-dev.local", 18080, "/app/homepage/api/services"): (
                    ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "application/json"},
                        body=(
                            '[{"name":"My First Group","services":['
                            '{"name":"My First Service","href":"http://homepage.local"}'
                            "]}]"
                        ),
                    )
                ),
            }
            return responses[(host, port, path)]

        service = self._service(
            route_strategy="path-prefix",
            routers={
                "homepage": {
                    "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/homepage")
                },
            },
            http_get=http_get,
        )

        with self.assertRaisesRegex(RuntimeError, "no gateway path-prefix tile links"):
            service.run(services={})

    def test_run_validates_lidarr_system_status_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_root = Path(tmp)
            lidarr_cfg = cfg_root / "lidarr"
            lidarr_cfg.mkdir(parents=True, exist_ok=True)
            (lidarr_cfg / "config.xml").write_text(
                "<Config><ApiKey>lidarr-key</ApiKey></Config>",
                encoding="utf-8",
            )

            def http_get(host: str, port: int, path: str, headers: dict[str, str]):
                responses = {
                    ("apps.media-dev.local", 18080, "/app/lidarr"): ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "text/plain"},
                        body="ok",
                    ),
                    (
                        "apps.media-dev.local",
                        18080,
                        "/app/lidarr/api/v1/system/status?apikey=lidarr-key",
                    ): ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "application/json"},
                        body='{"appName":"Lidarr","version":"3.1.0.4875"}',
                    ),
                }
                return responses[(host, port, path)]

            service = self._service(
                route_strategy="path-prefix",
                routers={
                    "lidarr": {
                        "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/lidarr")
                    },
                },
                compose_env={"CONFIG_ROOT": str(cfg_root)},
                http_get=http_get,
            )

            service.run(services={})

    def test_run_fails_when_lidarr_system_status_api_is_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_root = Path(tmp)
            lidarr_cfg = cfg_root / "lidarr"
            lidarr_cfg.mkdir(parents=True, exist_ok=True)
            (lidarr_cfg / "config.xml").write_text(
                "<Config><ApiKey>lidarr-key</ApiKey></Config>",
                encoding="utf-8",
            )

            def http_get(host: str, port: int, path: str, headers: dict[str, str]):
                responses = {
                    ("apps.media-dev.local", 18080, "/app/lidarr"): ComposeEdgeHttpResponse(
                        status=200,
                        headers={"Content-Type": "text/plain"},
                        body="ok",
                    ),
                    (
                        "apps.media-dev.local",
                        18080,
                        "/app/lidarr/api/v1/system/status?apikey=lidarr-key",
                    ): ComposeEdgeHttpResponse(
                        status=404,
                        headers={"Content-Type": "application/json"},
                        body='{"message":"missing"}',
                    ),
                }
                return responses[(host, port, path)]

            service = self._service(
                route_strategy="path-prefix",
                routers={
                    "lidarr": {
                        "rule": _route_rule(host="apps.media-dev.local", path_prefix="/app/lidarr")
                    },
                },
                compose_env={"CONFIG_ROOT": str(cfg_root)},
                http_get=http_get,
            )

            with self.assertRaisesRegex(RuntimeError, "system status API failed"):
                service.run(services={})


if __name__ == "__main__":
    unittest.main()
