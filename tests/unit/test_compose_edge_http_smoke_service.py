import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.compose.services.edge_http_smoke import (  # noqa: E402
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
        routers: dict[str, dict] | None = None,
        http_get=None,
    ) -> ComposeEdgeHttpSmokeService:
        label_service = mock.Mock()
        label_service.route_strategy.return_value = route_strategy
        label_service.cfg = SimpleNamespace(app_gateway_host=gateway_host)

        spec_resolver = mock.Mock()
        spec_resolver.compose_environment.return_value = {"APP_GATEWAY_PORT": str(gateway_port)}

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
        self.assertEqual(len(asset_calls), 1)
        self.assertEqual(
            asset_calls[0][3].get("Referer"),
            "http://apps.media-dev.local:18080/app/homepage",
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


if __name__ == "__main__":
    unittest.main()
