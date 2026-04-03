"""Compose edge HTTP smoke checks with route and asset validation."""

from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import parse

from core.platforms.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
from core.platforms.compose.services.labels import ComposeLabelService
from core.platforms.compose.services.spec import ComposeSpecResolver

_HOST_RULE_RE = re.compile(r"Host\((?P<body>[^)]*)\)", flags=re.IGNORECASE)
_PATH_PREFIX_RULE_RE = re.compile(r"PathPrefix\((?P<body>[^)]*)\)", flags=re.IGNORECASE)
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
_ASSET_ATTR_RE = re.compile(r"""(?:src|href)=["']([^"'#][^"']*)["']""", flags=re.IGNORECASE)
_EXTERNAL_SCHEME_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//", flags=re.IGNORECASE)
_OPTIONAL_ASSET_BASENAMES = {
    "apple-touch-icon.png",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "safari-pinned-tab.svg",
    "site.webmanifest",
}
_SERVARR_STATUS_API_VERSION = {
    "sonarr": "v3",
    "radarr": "v3",
    "lidarr": "v1",
    "readarr": "v1",
    "prowlarr": "v1",
}
_SERVARR_APP_NAME = {
    "sonarr": "Sonarr",
    "radarr": "Radarr",
    "lidarr": "Lidarr",
    "readarr": "Readarr",
    "prowlarr": "Prowlarr",
}
_XML_API_KEY_RE = re.compile(r"<ApiKey>([^<]+)</ApiKey>", flags=re.IGNORECASE)


def _extract_backtick_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.strip().lower()
        for token in _BACKTICK_TOKEN_RE.findall(str(value or ""))
        if str(token or "").strip()
    )


def _rule_hosts(rule: str) -> tuple[str, ...]:
    match = _HOST_RULE_RE.search(str(rule or ""))
    if not match:
        return ()
    return _extract_backtick_tokens(str(match.group("body") or ""))


def _rule_path_prefix(rule: str) -> str:
    match = _PATH_PREFIX_RULE_RE.search(str(rule or ""))
    if not match:
        return ""
    tokens = _extract_backtick_tokens(str(match.group("body") or ""))
    if not tokens:
        return ""
    value = str(tokens[0] or "").strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _normalize_port(value: object) -> int:
    token = str(value or "").strip()
    if token.startswith(":"):
        token = token[1:]
    if not token or not token.isdigit():
        return 80
    port = int(token)
    if port < 1 or port > 65535:
        return 80
    return port


def _response_header(headers: dict[str, str], key: str) -> str:
    token = str(key or "").strip().lower()
    for raw_key, raw_value in headers.items():
        if str(raw_key or "").strip().lower() == token:
            return str(raw_value or "")
    return ""


def _asset_is_optional(asset_ref: str) -> bool:
    path = parse.urlparse(str(asset_ref or "")).path
    base = str(path or "").rstrip("/").rsplit("/", 1)[-1].strip().lower()
    if not base:
        return False
    return base in _OPTIONAL_ASSET_BASENAMES


@dataclass(frozen=True)
class ComposeEdgeHttpResponse:
    status: int
    headers: dict[str, str]
    body: str


HttpGetFn = Callable[[str, int, str, dict[str, str]], ComposeEdgeHttpResponse]
InfoFn = Callable[[str], None]


@dataclass
class ComposeEdgeHttpSmokeService:
    label_service: ComposeLabelService
    spec_resolver: ComposeSpecResolver
    route_graph_service: ComposeEdgeRouteGraphService
    info: InfoFn
    http_get: HttpGetFn | None = None

    def _http_get(
        self,
        *,
        host_header: str,
        port: int,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> ComposeEdgeHttpResponse:
        if self.http_get is not None:
            return self.http_get(host_header, port, path, dict(headers or {}))

        request_headers = {"Host": host_header, "User-Agent": "media-stack-compose-smoke"}
        request_headers.update(dict(headers or {}))
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
        try:
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            body = response.read(512_000).decode("utf-8", errors="replace")
            headers_out = {str(key): str(value) for key, value in response.getheaders()}
            return ComposeEdgeHttpResponse(
                status=int(response.status),
                headers=headers_out,
                body=body,
            )
        finally:
            conn.close()

    def _compose_gateway_http_port(self) -> int:
        env = self.spec_resolver.compose_environment()
        return _normalize_port(
            env.get("APP_GATEWAY_PORT")
            or env.get("EDGE_HTTP_PORT")
            or env.get("TRAEFIK_HTTP_PORT")
            or "80"
        )

    def _compose_config_root(self) -> Path | None:
        env = self.spec_resolver.compose_environment()
        token = str(env.get("CONFIG_ROOT") or "").strip()
        if not token:
            return None
        return Path(token)

    def _path_prefix_routes(self, services: dict[str, dict[str, Any]]) -> tuple[str, ...]:
        rendered = self.route_graph_service.render(services)
        routers = dict((rendered.payload.get("http") or {}).get("routers") or {})
        gateway_host = str(self.label_service.cfg.app_gateway_host or "").strip().lower()
        out: list[str] = []
        seen: set[str] = set()
        for router_cfg in routers.values():
            if not isinstance(router_cfg, dict):
                continue
            rule = str(router_cfg.get("rule") or "").strip()
            if not rule:
                continue
            hosts = _rule_hosts(rule)
            if gateway_host and hosts and gateway_host not in hosts:
                continue
            prefix = _rule_path_prefix(rule)
            if not prefix or prefix in seen:
                continue
            seen.add(prefix)
            out.append(prefix)
        return tuple(sorted(out))

    @staticmethod
    def _is_html_response(headers: dict[str, str]) -> bool:
        content_type = _response_header(headers, "content-type").lower()
        return "text/html" in content_type or "application/xhtml+xml" in content_type

    @staticmethod
    def _asset_candidates(body: str) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for match in _ASSET_ATTR_RE.finditer(str(body or "")):
            raw = str(match.group(1) or "").strip()
            if not raw or raw.startswith("#"):
                continue
            lower = raw.lower()
            if lower.startswith(("data:", "javascript:", "mailto:")):
                continue
            if _EXTERNAL_SCHEME_RE.match(raw):
                continue
            if raw in seen:
                continue
            seen.add(raw)
            out.append(raw)
            if len(out) >= 6:
                break
        return tuple(out)

    @staticmethod
    def _path_from_url(url: str) -> str:
        parsed = parse.urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path

    def _follow_redirects(
        self,
        *,
        host_header: str,
        port: int,
        initial_path: str,
        max_hops: int = 3,
    ) -> tuple[str, ComposeEdgeHttpResponse, list[str]]:
        path = str(initial_path or "/")
        visited: list[str] = [path]
        response = self._http_get(host_header=host_header, port=port, path=path)
        hops = 0
        while response.status in {301, 302, 303, 307, 308} and hops < max_hops:
            location = _response_header(response.headers, "location").strip()
            if not location:
                break
            base_url = f"http://{host_header}:{port}{path if path.startswith('/') else '/' + path}"
            resolved = parse.urljoin(base_url, location)
            next_path = self._path_from_url(resolved)
            if next_path in visited:
                break
            visited.append(next_path)
            path = next_path
            response = self._http_get(host_header=host_header, port=port, path=path)
            hops += 1
        return path, response, visited

    def _check_path(
        self,
        *,
        host_header: str,
        gateway_port: int,
        route_path: str,
        failures: list[str],
    ) -> None:
        final_path, response, visited = self._follow_redirects(
            host_header=host_header,
            port=gateway_port,
            initial_path=route_path,
        )
        if response.status in {401, 403}:
            failures.append(
                f"{route_path}: final status {response.status} (visited={','.join(visited)})"
            )
            return
        if response.status >= 400:
            failures.append(
                f"{route_path}: final status {response.status} (visited={','.join(visited)})"
            )
            return

        if not final_path.startswith(route_path):
            failures.append(
                f"{route_path}: redirect/path escape to '{final_path}' "
                f"(visited={','.join(visited)})"
            )
            return

        final_path_lower = final_path.lower()
        if "/wizard/" in final_path_lower or final_path_lower.endswith("/wizard"):
            failures.append(
                f"{route_path}: landed on startup/setup wizard path '{final_path}' "
                f"(visited={','.join(visited)})"
            )
            return

        if not self._is_html_response(response.headers):
            return

        base_url = f"http://{host_header}:{gateway_port}{final_path}"
        for asset_ref in self._asset_candidates(response.body):
            resolved_url = parse.urljoin(base_url, asset_ref)
            resolved_path = self._path_from_url(resolved_url)
            if parse.urlparse(resolved_url).hostname not in {"", host_header}:
                continue
            asset_response = self._http_get(
                host_header=host_header,
                port=gateway_port,
                path=resolved_path,
                headers={
                    "Accept": "*/*",
                    "Referer": base_url,
                },
            )
            if asset_response.status in {401, 403}:
                failures.append(
                    f"{route_path}: asset '{asset_ref}' failed with "
                    f"HTTP {asset_response.status} via '{resolved_path}'"
                )
                return
            if asset_response.status == 404 and _asset_is_optional(asset_ref):
                continue
            if asset_response.status >= 400:
                failures.append(
                    f"{route_path}: asset '{asset_ref}' failed with "
                    f"HTTP {asset_response.status} via '{resolved_path}'"
                )
                return

    @staticmethod
    def _route_service_name(route_path: str) -> str:
        token = str(route_path or "").strip().rstrip("/")
        if not token:
            return ""
        return token.rsplit("/", 1)[-1].strip().lower()

    def _read_servarr_api_key(self, *, config_root: Path, service_name: str) -> str:
        config_path = config_root / service_name / "config.xml"
        if not config_path.exists():
            return ""
        text = config_path.read_text(encoding="utf-8", errors="replace")
        match = _XML_API_KEY_RE.search(text)
        if not match:
            return ""
        return str(match.group(1) or "").strip()

    def _check_servarr_system_status_api(
        self,
        *,
        host_header: str,
        gateway_port: int,
        route_path: str,
        config_root: Path | None,
        failures: list[str],
    ) -> None:
        service_name = self._route_service_name(route_path)
        api_version = _SERVARR_STATUS_API_VERSION.get(service_name)
        if not api_version:
            return
        if config_root is None:
            failures.append(
                f"{route_path}: cannot validate system status API; CONFIG_ROOT is missing."
            )
            return
        api_key = self._read_servarr_api_key(config_root=config_root, service_name=service_name)
        if not api_key:
            failures.append(
                f"{route_path}: cannot validate system status API; "
                f"ApiKey missing in {config_root / service_name / 'config.xml'}."
            )
            return

        status_path = (
            f"{route_path}/api/{api_version}/system/status?apikey={parse.quote(api_key, safe='')}"
        )
        response = self._http_get(
            host_header=host_header,
            port=gateway_port,
            path=status_path,
            headers={"Accept": "application/json"},
        )
        if response.status >= 400:
            failures.append(
                f"{route_path}: system status API failed with "
                f"HTTP {response.status} via '{status_path}'"
            )
            return
        content_type = _response_header(response.headers, "content-type").lower()
        if "json" not in content_type:
            failures.append(
                f"{route_path}: system status API returned non-JSON content-type "
                f"'{content_type or '<missing>'}' via '{status_path}'"
            )
            return
        try:
            payload = json.loads(str(response.body or ""))
        except Exception:
            failures.append(
                f"{route_path}: system status API returned invalid JSON via '{status_path}'"
            )
            return
        if not isinstance(payload, dict):
            failures.append(
                f"{route_path}: system status API returned non-object payload via '{status_path}'"
            )
            return
        version = str(payload.get("version") or "").strip()
        if not version:
            failures.append(
                f"{route_path}: system status API payload missing version via '{status_path}'"
            )
            return
        expected_name = _SERVARR_APP_NAME.get(service_name, "")
        app_name = str(payload.get("appName") or "").strip()
        if expected_name and app_name and app_name.lower() != expected_name.lower():
            failures.append(
                f"{route_path}: system status API returned appName='{app_name}' "
                f"(expected '{expected_name}') via '{status_path}'"
            )
            return

    def _homepage_tile_paths(
        self,
        *,
        gateway_host: str,
        gateway_port: int,
        app_prefix: str,
    ) -> tuple[str, ...]:
        api_path = f"{app_prefix}/homepage/api/services"
        response = self._http_get(
            host_header=gateway_host,
            port=gateway_port,
            path=api_path,
            headers={"Accept": "application/json"},
        )
        if response.status >= 400:
            raise RuntimeError(
                "Compose homepage tile validation failed: "
                f"services API returned HTTP {response.status} at {api_path}."
            )
        try:
            payload = json.loads(str(response.body or ""))
        except Exception as exc:
            raise RuntimeError(
                "Compose homepage tile validation failed: "
                f"services API payload is not valid JSON ({exc})."
            ) from exc
        if not isinstance(payload, list):
            raise RuntimeError(
                "Compose homepage tile validation failed: " "services API payload is not a list."
            )

        out: list[str] = []
        discovered_hrefs: list[str] = []
        seen: set[str] = set()
        for group in payload:
            if not isinstance(group, dict):
                continue
            services = group.get("services")
            if not isinstance(services, list):
                continue
            for service in services:
                if not isinstance(service, dict):
                    continue
                href = str(service.get("href") or "").strip()
                if not href:
                    continue
                discovered_hrefs.append(href)
                parsed = parse.urlparse(href if "://" in href else f"http://{href}")
                href_host = str(parsed.hostname or "").strip().lower()
                if href_host != gateway_host:
                    continue
                link_path = self._path_from_url(href)
                if not link_path.startswith(f"{app_prefix}/"):
                    continue
                if link_path in seen:
                    continue
                seen.add(link_path)
                out.append(link_path)
        if not out:
            sample = ", ".join(discovered_hrefs[:5]) if discovered_hrefs else "<none>"
            raise RuntimeError(
                "Compose homepage tile validation failed: "
                "services API returned no gateway path-prefix tile links "
                f"(expected host={gateway_host} with prefix {app_prefix}/..., "
                f"sample_hrefs={sample})."
            )
        return tuple(out)

    def run(self, services: dict[str, dict[str, Any]]) -> None:
        strategy = self.label_service.route_strategy()
        gateway_host = str(self.label_service.cfg.app_gateway_host or "").strip().lower()
        if strategy not in {"path-prefix", "hybrid"} or not gateway_host:
            return

        routes = self._path_prefix_routes(services)
        if not routes:
            return

        gateway_port = self._compose_gateway_http_port()
        app_prefix = str(self.label_service.cfg.app_path_prefix or "").strip()
        if not app_prefix:
            app_prefix = "/app"
        if not app_prefix.startswith("/"):
            app_prefix = f"/{app_prefix}"
        app_prefix = app_prefix.rstrip("/") or "/app"
        failures: list[str] = []
        checked_routes = 0
        config_root = self._compose_config_root()
        for route_path in routes:
            checked_routes += 1
            self._check_path(
                host_header=gateway_host,
                gateway_port=gateway_port,
                route_path=route_path,
                failures=failures,
            )
            self._check_servarr_system_status_api(
                host_header=gateway_host,
                gateway_port=gateway_port,
                route_path=route_path,
                config_root=config_root,
                failures=failures,
            )

        homepage_path = f"{app_prefix}/homepage"
        checked_links = 0
        if homepage_path in set(routes):
            tile_paths = self._homepage_tile_paths(
                gateway_host=gateway_host,
                gateway_port=gateway_port,
                app_prefix=app_prefix,
            )
            for tile_path in tile_paths:
                checked_links += 1
                self._check_path(
                    host_header=gateway_host,
                    gateway_port=gateway_port,
                    route_path=tile_path,
                    failures=failures,
                )

        self.info(
            "Compose edge HTTP smoke check: "
            f"validated {checked_routes} path route(s) and {checked_links} homepage tile link(s) "
            f"via {gateway_host}:{gateway_port}."
        )
        if failures:
            sample = "; ".join(failures[:5])
            raise RuntimeError(
                "Compose edge HTTP smoke validation failed. "
                "Routes are reachable but page navigation/assets are broken: "
                f"{sample}"
            )
