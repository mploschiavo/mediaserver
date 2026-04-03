"""Compose edge HTTP smoke checks with route and asset validation."""

from __future__ import annotations

import http.client
import re
from dataclasses import dataclass
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

    def run(self, services: dict[str, dict[str, Any]]) -> None:
        strategy = self.label_service.route_strategy()
        gateway_host = str(self.label_service.cfg.app_gateway_host or "").strip().lower()
        if strategy not in {"path-prefix", "hybrid"} or not gateway_host:
            return

        routes = self._path_prefix_routes(services)
        if not routes:
            return

        gateway_port = self._compose_gateway_http_port()
        failures: list[str] = []
        checked_routes = 0
        for route_path in routes:
            checked_routes += 1
            final_path, response, visited = self._follow_redirects(
                host_header=gateway_host,
                port=gateway_port,
                initial_path=route_path,
            )
            if response.status in {401, 403}:
                continue
            if response.status >= 400:
                failures.append(
                    f"{route_path}: final status {response.status} (visited={','.join(visited)})"
                )
                continue

            if not final_path.startswith(route_path):
                failures.append(
                    f"{route_path}: redirect/path escape to '{final_path}' "
                    f"(visited={','.join(visited)})"
                )
                continue

            if not self._is_html_response(response.headers):
                continue

            base_url = f"http://{gateway_host}:{gateway_port}{final_path}"
            for asset_ref in self._asset_candidates(response.body):
                resolved_url = parse.urljoin(base_url, asset_ref)
                resolved_path = self._path_from_url(resolved_url)
                if parse.urlparse(resolved_url).hostname not in {"", gateway_host}:
                    continue
                asset_response = self._http_get(
                    host_header=gateway_host,
                    port=gateway_port,
                    path=resolved_path,
                    headers={
                        "Accept": "*/*",
                        "Referer": base_url,
                    },
                )
                if asset_response.status in {401, 403}:
                    continue
                if asset_response.status >= 400:
                    failures.append(
                        f"{route_path}: asset '{asset_ref}' failed with "
                        f"HTTP {asset_response.status} via '{resolved_path}'"
                    )
                    break

        self.info(
            "Compose edge HTTP smoke check: "
            f"validated {checked_routes} path route(s) via {gateway_host}:{gateway_port}."
        )
        if failures:
            sample = "; ".join(failures[:5])
            raise RuntimeError(
                "Compose edge HTTP smoke validation failed. "
                "Routes are reachable but page navigation/assets are broken: "
                f"{sample}"
            )
