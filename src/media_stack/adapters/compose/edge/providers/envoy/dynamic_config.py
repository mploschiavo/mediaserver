"""Render Envoy runtime config from normalized compose edge labels."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import copy
import os
import re

from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateService as _TlsCertificateService,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from media_stack.services.apps.stack.routing_defaults import (
    DEFAULT_REDIRECT_PRIORITY_SLUG as _PRIORITY_SLUG,
    DEFAULT_REDIRECT_FALLBACK_SLUG as _FALLBACK_SLUG,
    APP_ROOT_DASHBOARD_SLUG as _DASHBOARD_SLUG,
)
from media_stack.adapters.compose.edge.providers.envoy.clusters import (
    build_clusters_from_service_map,
)
from media_stack.adapters.compose.edge.providers.envoy.helpers import (
    _cluster_name,
    _extract_backtick_tokens,
    _path_prefix_app_slug,
    _path_prefix_root,
    _rule_hosts,
    _rule_path_prefix,
    _session_cookie_name,
    _strip_prefix_value,
    _tokenize,
    _virtual_host_name,
)
from media_stack.adapters.compose.edge.providers.envoy.routes import (
    cookie_fallback_route_cfg,
    fallback_regex_rewrite,
    html_accept_header_match,
    primary_route_cfg,
    referer_fallback_route_cfg,
)
from media_stack.adapters.compose.edge.providers.envoy.virtual_hosts import (
    build_virtual_hosts,
)
from media_stack.adapters.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
from media_stack.adapters.compose.services.spec import ComposeSpecResolver
from media_stack.core.auth.gateway_policy import GatewayAuthPolicy
from media_stack.core.auth.envoy_ext_authz import (
    apply_per_route_auth_policy,
    inject_ext_authz_into_payload,
)
import logging

_DEFAULT_TEMPLATE_RELATIVE_PATH = Path("config/defaults/compose/envoy.runtime.base.yaml")
_PRIMARY_ROUTE_RANK_BASE = 3000
_HTML_FALLBACK_REDIRECT_ROUTE_RANK_BASE = 2500
_REFERER_FALLBACK_ROUTE_RANK_BASE = 2000
_COOKIE_FALLBACK_ROUTE_RANK_BASE = 1000
_DEFAULT_HTML_REDIRECT_ROUTE_RANK = 0

# Envoy route-action keys emitted >=5 times inside this module. Using
# named constants avoids the duplicate-strings ratchet flagging them
# and keeps one source of truth if Envoy ever renames the field.
_KEY_PATH_REDIRECT = "path_redirect"


@dataclass(frozen=True)
class EnvoyDynamicConfigRender:
    payload: dict[str, Any]
    route_count: int
    cluster_count: int
    ignored_redirect_middleware_count: int


TemplateLoaderFn = Callable[[Path], dict[str, Any]]


@dataclass
class EnvoyDynamicConfigService:
    route_graph_service: ComposeEdgeRouteGraphService
    spec_resolver: ComposeSpecResolver
    runtime_template_path: Path | None = None
    template_loader: TemplateLoaderFn | None = None
    auth_policy: GatewayAuthPolicy | None = None

    @staticmethod
    def _candidate_template_roots() -> list[Path]:
        """Plausible bases for ``config/defaults/compose/...``.

        ``Path(__file__).resolve().parents[8]`` worked when the
        repo was checked out alongside the source tree (parents counted
        through ``src/media_stack/adapters/compose/edge/providers/envoy``
        landing on the repo root). Once the controller is shipped as
        a wheel installed under ``site-packages/``, parents[8] points
        at ``/usr/local/lib`` and the lookup fails — this is what
        wedged auth on K8s (the ``envoy-config`` job errored, so the
        ``ext_authz`` filter was never injected into the live envoy.yaml,
        so every request bypassed Authelia).

        The fix is to try several roots in order and return the first
        one that contains the template. Order:
          1. ``parents[8]`` — original behavior, still right for
             source-tree runs.
          2. ``/opt/media-stack`` — image WORKDIR baked into both the
             compose and K8s controller container layouts.
          3. CWD — last-resort for ad-hoc invocations.
        """
        roots: list[Path] = []
        seen: set[Path] = set()

        def add(p: Path) -> None:
            try:
                resolved = p.resolve()
            except OSError:
                return
            if resolved not in seen:
                seen.add(resolved)
                roots.append(resolved)

        try:
            add(Path(__file__).resolve().parents[8])
        except IndexError:
            pass
        add(Path("/opt/media-stack"))
        add(Path.cwd())
        return roots

    @classmethod
    def _repo_root(cls) -> Path:
        """First candidate that actually contains the canonical template.
        Falls back to the legacy parents[8] computation so callers that
        deliberately resolve other paths against the repo root stay
        consistent with previous behavior even on filesystems where
        the template is missing."""
        for root in cls._candidate_template_roots():
            if (root / _DEFAULT_TEMPLATE_RELATIVE_PATH).is_file():
                return root
        return Path(__file__).resolve().parents[8]

    def _resolve_runtime_template_path(self) -> Path:
        if self.runtime_template_path is not None:
            candidate = Path(self.runtime_template_path).expanduser()
        else:
            env = self.spec_resolver.compose_environment()
            token = str(env.get("ENVOY_RUNTIME_TEMPLATE_FILE") or "").strip()
            if token:
                candidate = Path(token).expanduser()
            else:
                candidate = self._repo_root() / _DEFAULT_TEMPLATE_RELATIVE_PATH

        if candidate.is_absolute():
            return candidate
        return (self._repo_root() / candidate).resolve()

    def _load_runtime_template_payload(self) -> dict[str, Any]:
        template_path = self._resolve_runtime_template_path()
        if self.template_loader is not None:
            payload = self.template_loader(template_path)
        else:
            if not template_path.exists():
                raise RuntimeError(
                    "Envoy runtime template file not found: "
                    f"{template_path}. Set ENVOY_RUNTIME_TEMPLATE_FILE to override."
                )
            payload = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(
                "Envoy runtime template must deserialize to a mapping payload: " f"{template_path}"
            )
        return copy.deepcopy(payload)

    @staticmethod
    def _resolve_route_config(payload: dict[str, Any]) -> dict[str, Any]:
        static_resources = payload.get("static_resources")
        if not isinstance(static_resources, dict):
            raise RuntimeError("Envoy runtime template is missing static_resources mapping.")
        listeners = static_resources.get("listeners")
        if not isinstance(listeners, list) or not listeners:
            raise RuntimeError("Envoy runtime template must declare at least one listener.")
        listener = listeners[0]
        if not isinstance(listener, dict):
            raise RuntimeError("Envoy runtime template listener entry must be a mapping.")
        filter_chains = listener.get("filter_chains")
        if not isinstance(filter_chains, list) or not filter_chains:
            raise RuntimeError("Envoy runtime template listener must include filter_chains.")
        first_chain = filter_chains[0]
        if not isinstance(first_chain, dict):
            raise RuntimeError("Envoy runtime template filter chain entry must be a mapping.")
        filters = first_chain.get("filters")
        if not isinstance(filters, list) or not filters:
            raise RuntimeError("Envoy runtime template filter chain must include filters.")
        first_filter = filters[0]
        if not isinstance(first_filter, dict):
            raise RuntimeError("Envoy runtime template filter entry must be a mapping.")
        typed_config = first_filter.get("typed_config")
        if not isinstance(typed_config, dict):
            raise RuntimeError(
                "Envoy runtime template http_connection_manager typed_config is missing."
            )
        route_config = typed_config.get("route_config")
        if not isinstance(route_config, dict):
            raise RuntimeError(
                "Envoy runtime template typed_config must include route_config mapping."
            )
        return route_config

    @staticmethod
    def _replace_virtual_hosts(
        payload: dict[str, Any], virtual_hosts: list[dict[str, Any]]
    ) -> None:
        route_config = EnvoyDynamicConfigService._resolve_route_config(payload)
        route_config["virtual_hosts"] = list(virtual_hosts)

    @staticmethod
    def _replace_clusters(payload: dict[str, Any], clusters: list[dict[str, Any]]) -> None:
        static_resources = payload.get("static_resources")
        if not isinstance(static_resources, dict):
            raise RuntimeError("Envoy runtime template is missing static_resources mapping.")
        static_resources["clusters"] = list(clusters)

    def _apply_route_auth(self, route: dict[str, Any], service_name: str) -> None:
        """Apply per-route auth policy if gateway auth is active."""
        if self.auth_policy and self.auth_policy.ext_authz:
            apply_per_route_auth_policy(route, service_name, self.auth_policy)

    def render(self, services: dict[str, dict[str, Any]]) -> EnvoyDynamicConfigRender:
        route_graph = self.route_graph_service.render(services)
        http_cfg = dict(route_graph.payload.get("http") or {})
        routers = dict(http_cfg.get("routers") or {})
        middlewares = dict(http_cfg.get("middlewares") or {})
        service_map = dict(http_cfg.get("services") or {})

        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        default_html_redirect_by_host: dict[str, str] = {}
        ignored_redirect_middleware_count = self._collect_routes_from_routers(
            routers, middlewares, routes_by_host, default_html_redirect_by_host,
        )
        self._append_default_html_fallback_routes(
            routes_by_host, default_html_redirect_by_host,
        )
        self._append_controller_path_aliases(routes_by_host)
        self._append_oidc_login_alias(routes_by_host)

        clusters = build_clusters_from_service_map(service_map)
        auth_vhost = self._build_auth_portal_vhost(routes_by_host, clusters)
        virtual_hosts, route_count = build_virtual_hosts(routes_by_host)
        route_count = self._insert_auth_vhost(virtual_hosts, auth_vhost, route_count)

        if self.auth_policy and self.auth_policy.ext_authz:
            self._apply_auth_to_virtual_hosts(virtual_hosts, service_map)

        payload = self._assemble_payload(virtual_hosts, clusters, auth_vhost)
        return EnvoyDynamicConfigRender(
            payload=payload,
            route_count=route_count,
            cluster_count=len(clusters),
            ignored_redirect_middleware_count=ignored_redirect_middleware_count,
        )

    @staticmethod
    def _insert_auth_vhost(
        virtual_hosts: list[dict[str, Any]],
        auth_vhost: dict[str, Any] | None,
        route_count: int,
    ) -> int:
        """Splice the auth-portal vhost in front of the catch-all vhost.

        The original inline block mutated ``virtual_hosts`` and bumped
        the route count in one step; a named helper makes that coupling
        explicit and keeps ``render`` focused on the assembly pipeline.
        """
        if auth_vhost is None:
            return route_count
        catchall_idx = next(
            (i for i, vh in enumerate(virtual_hosts) if vh.get("name") == "vhost_catchall"),
            len(virtual_hosts),
        )
        virtual_hosts.insert(catchall_idx, auth_vhost)
        return route_count + 1

    def _assemble_payload(
        self,
        virtual_hosts: list[dict[str, Any]],
        clusters: list[dict[str, Any]],
        auth_vhost: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Load the runtime template and stamp in virtual hosts, clusters, authz, TLS.

        Collapses the tail of ``render`` into a single call so the
        caller can focus on the pre-assembly data transformations.
        """
        payload = self._load_runtime_template_payload()
        self._replace_virtual_hosts(payload, virtual_hosts)
        self._replace_clusters(payload, clusters)
        if self.auth_policy and self.auth_policy.ext_authz:
            # Build auth portal URL for Authelia redirect parameter
            auth_portal = ""
            if auth_vhost is not None:
                auth_domains = auth_vhost.get("domains", [])
                if auth_domains:
                    auth_portal = f"https://{auth_domains[0]}"
            inject_ext_authz_into_payload(payload, self.auth_policy, auth_portal)
        # Inject TLS transport socket if cert files exist (compose with TLS).
        # K8s uses ingress TLS termination, so certs won't be present.
        self._inject_tls_if_available(payload)
        # XFF trusted hops — env-overridable. The template hardcodes
        # 1 (single proxy: K8s Ingress controller / nginx). Operators
        # behind Cloudflare set MEDIA_STACK_TRUSTED_PROXY_HOPS=2 so
        # CF's hop is also trusted; an extra CDN in front would need 3.
        # Setting too high lets clients spoof their IP via XFF; setting
        # too low leaves operators staring at proxy IPs in the panel.
        self._apply_xff_trusted_hops_override(payload)
        return payload

    def _apply_xff_trusted_hops_override(
        self, payload: dict[str, Any],
    ) -> None:
        """Read MEDIA_STACK_TRUSTED_PROXY_HOPS from env and write the
        value onto every HCM in the payload. Best-effort: structural
        anomalies (missing keys, wrong types) silently skip — the
        template already has a sane default."""
        import os as _os
        raw = _os.environ.get("MEDIA_STACK_TRUSTED_PROXY_HOPS", "").strip()
        if not raw:
            return
        try:
            hops = int(raw)
        except ValueError:
            return
        if hops < 1 or hops > 10:
            return
        for listener in (
            (payload.get("static_resources") or {}).get("listeners") or []
        ):
            for chain in listener.get("filter_chains", []) or []:
                for f in chain.get("filters", []) or []:
                    cfg = f.get("typed_config") or {}
                    if (
                        cfg.get("@type", "").endswith(
                            ".HttpConnectionManager",
                        )
                        and "use_remote_address" in cfg
                    ):
                        cfg["xff_num_trusted_hops"] = hops

    def _collect_routes_from_routers(
        self,
        routers: dict[str, Any],
        middlewares: dict[str, Any],
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        default_html_redirect_by_host: dict[str, str],
    ) -> int:
        """Translate every router entry into primary/HTML/fallback Envoy routes.

        Pulled out of ``render`` so the main method reads as the high-level
        pipeline (collect → default redirects → aliases → assemble) instead of
        one long procedural wall.  Returns the count of ignored
        ``redirectRegex`` middlewares so the caller can surface it in the
        render result.
        """
        ignored_redirect_middleware_count = 0
        for router_name in sorted(routers.keys()):
            router_cfg = routers.get(router_name) or {}
            if not isinstance(router_cfg, dict):
                continue
            ignored_redirect_middleware_count += self._process_router_entry(
                router_name=router_name,
                router_cfg=router_cfg,
                middlewares=middlewares,
                routes_by_host=routes_by_host,
                default_html_redirect_by_host=default_html_redirect_by_host,
            )
        return ignored_redirect_middleware_count

    def _process_router_entry(
        self,
        *,
        router_name: str,
        router_cfg: dict[str, Any],
        middlewares: dict[str, Any],
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        default_html_redirect_by_host: dict[str, str],
    ) -> int:
        """Emit all Envoy routes for a single router entry (all hosts × variants).

        Split from ``_collect_routes_from_routers`` so the outer loop is a
        pure iteration and this method owns the per-router decisions
        (hosts, service name, strip prefix, regex rewrite, rank bases).
        """
        rule = str(router_cfg.get("rule") or "").strip()
        hosts = _rule_hosts(rule)
        if not hosts:
            return 0
        path_prefix = _rule_path_prefix(rule) or "/"

        service_name = str(router_cfg.get("service") or "").strip()
        if not service_name:
            service_name = str(router_name or "").strip()
        if not service_name:
            return 0
        cluster_name_val = _cluster_name(service_name)

        strip_prefix, ignored_delta = self._middleware_strip_prefix(
            router_cfg, middlewares,
        )
        needs_strip, strip_prefix = self._resolve_needs_strip(
            service_name, path_prefix, strip_prefix,
        )
        regex_rewrite = self._regex_rewrite_for(path_prefix, needs_strip)

        for host in hosts:
            host_token = str(host or "").strip().lower()
            if not host_token:
                continue
            self._emit_router_routes_for_host(
                host_token=host_token,
                path_prefix=path_prefix,
                cluster_name_val=cluster_name_val,
                strip_prefix=strip_prefix,
                regex_rewrite=regex_rewrite,
                routes_by_host=routes_by_host,
                default_html_redirect_by_host=default_html_redirect_by_host,
            )
        return ignored_delta

    @classmethod
    def _emit_router_routes_for_host(
        cls,
        *,
        host_token: str,
        path_prefix: str,
        cluster_name_val: str,
        strip_prefix: str,
        regex_rewrite: dict[str, Any] | None,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        default_html_redirect_by_host: dict[str, str],
    ) -> None:
        """Emit the primary/HTML/fallback routes for one (router, host) pair.

        Extracted so the outer host-loop has no per-host logic inline —
        every host does the same work, driven by the rank constants
        derived here from ``path_prefix``.
        """
        rank = len(path_prefix)
        primary_rank = _PRIMARY_ROUTE_RANK_BASE + rank
        html_primary_rank = primary_rank + 1
        referer_fallback_rank = _REFERER_FALLBACK_ROUTE_RANK_BASE + rank
        cookie_fallback_rank = _COOKIE_FALLBACK_ROUTE_RANK_BASE + rank
        cls._append_primary_and_html_routes(
            routes_by_host=routes_by_host,
            host_token=host_token,
            path_prefix=path_prefix,
            cluster_name_val=cluster_name_val,
            regex_rewrite=regex_rewrite,
            primary_rank=primary_rank,
            html_primary_rank=html_primary_rank,
        )
        if path_prefix and path_prefix != "/":
            cls._update_default_html_redirect(
                default_html_redirect_by_host, host_token, path_prefix,
            )
            cls._append_prefix_fallback_routes(
                routes_by_host=routes_by_host,
                host_token=host_token,
                path_prefix=path_prefix,
                cluster_name_val=cluster_name_val,
                strip_prefix=strip_prefix,
                referer_fallback_rank=referer_fallback_rank,
                cookie_fallback_rank=cookie_fallback_rank,
            )

    @staticmethod
    def _middleware_strip_prefix(
        router_cfg: dict[str, Any], middlewares: dict[str, Any],
    ) -> tuple[str, int]:
        """Scan router middlewares for stripPrefix + count ignored redirectRegex.

        Extracted because the middleware loop doesn't need router-level
        state beyond the two return values, and isolating it keeps
        ``_collect_routes_from_routers`` readable.
        """
        strip_prefix = ""
        ignored = 0
        router_middlewares = router_cfg.get("middlewares")
        if isinstance(router_middlewares, list):
            middleware_names = [str(item or "").strip() for item in router_middlewares]
        else:
            middleware_names = []
        for middleware_name in middleware_names:
            if not middleware_name:
                continue
            middleware_cfg = middlewares.get(middleware_name)
            if not isinstance(middleware_cfg, dict):
                continue
            if "redirectRegex" in middleware_cfg:
                ignored += 1
            if not strip_prefix:
                strip_prefix = _strip_prefix_value(middleware_cfg)
        return strip_prefix, ignored

    @staticmethod
    def _resolve_needs_strip(
        service_name: str, path_prefix: str, strip_prefix: str,
    ) -> tuple[bool, str]:
        """Combine legacy-label and registry signals into a single strip decision.

        Two sources can demand prefix stripping (label-based middleware and
        the service registry flag); keeping the branching in a helper means
        the caller can treat "needs_strip" as an opaque yes/no answer.
        """
        needs_strip = bool(strip_prefix and strip_prefix == path_prefix)
        if not needs_strip and path_prefix != "/":
            try:
                from media_stack.api.services.registry import get_service
                svc = get_service(service_name)
                if svc and not svc.preserve_path_prefix:
                    needs_strip = True
                    strip_prefix = path_prefix  # propagate to fallback routes
            except Exception as exc:
                log_swallowed(exc)
        return needs_strip, strip_prefix

    @staticmethod
    def _regex_rewrite_for(path_prefix: str, needs_strip: bool) -> dict[str, Any] | None:
        """Build the regex_rewrite payload only when a real prefix strip is needed."""
        if needs_strip and path_prefix != "/":
            return {
                "pattern": {
                    "google_re2": {},
                    "regex": f"^{re.escape(path_prefix)}/?(.*)$",
                },
                "substitution": r"/\1",
            }
        return None

    @classmethod
    def _append_primary_and_html_routes(
        cls,
        *,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        path_prefix: str,
        cluster_name_val: str,
        regex_rewrite: dict[str, Any] | None,
        primary_rank: int,
        html_primary_rank: int,
    ) -> None:
        """Emit the primary route, trailing-slash redirect, and HTML-only clone.

        Keeps the per-host mutation logic together so ``_collect_routes_from_routers``
        stays a linear flow over routers rather than a nested procedural block.
        """
        cls._append_primary_route(
            routes_by_host=routes_by_host,
            host_token=host_token,
            path_prefix=path_prefix,
            cluster_name_val=cluster_name_val,
            regex_rewrite=regex_rewrite,
            primary_rank=primary_rank,
        )
        cls._append_html_variant_route(
            routes_by_host=routes_by_host,
            host_token=host_token,
            path_prefix=path_prefix,
            cluster_name_val=cluster_name_val,
            regex_rewrite=regex_rewrite,
            html_primary_rank=html_primary_rank,
        )

    @staticmethod
    def _append_primary_route(
        *,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        path_prefix: str,
        cluster_name_val: str,
        regex_rewrite: dict[str, Any] | None,
        primary_rank: int,
    ) -> None:
        """Emit the non-HTML primary route plus the trailing-slash redirect.

        Separated from the HTML variant so each method handles one accept
        profile — easier to reason about match/rewrite rules per variant.
        """
        route_cfg = primary_route_cfg(
            host=host_token,
            path_prefix=path_prefix,
            cluster_name=cluster_name_val,
            include_session_cookie=False,
        )
        if regex_rewrite is not None:
            route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
        routes_by_host.setdefault(host_token, []).append((primary_rank, dict(route_cfg)))
        # Redirect /app/service (no trailing slash) → /app/service/
        # so relative URLs in SPAs resolve correctly.
        if path_prefix and path_prefix != "/" and not path_prefix.endswith("/"):
            routes_by_host.setdefault(host_token, []).append((
                primary_rank + 2,
                {
                    "match": {"path": path_prefix},
                    "redirect": {_KEY_PATH_REDIRECT: path_prefix + "/"},
                },
            ))

    @staticmethod
    def _append_html_variant_route(
        *,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        path_prefix: str,
        cluster_name_val: str,
        regex_rewrite: dict[str, Any] | None,
        html_primary_rank: int,
    ) -> None:
        """Emit the HTML-accept-only route that enables session-cookie forwarding.

        The HTML variant needs an extra ``accept: text/html`` header match
        and an uncompressed upstream flag; isolating it keeps those
        branches out of the plain-API route emitter.
        """
        html_route_cfg = primary_route_cfg(
            host=host_token,
            path_prefix=path_prefix,
            cluster_name=cluster_name_val,
            include_session_cookie=True,
            prefer_uncompressed_upstream=True,
        )
        if regex_rewrite is not None:
            html_route_cfg["route"]["regex_rewrite"] = dict(regex_rewrite)
        html_match = dict(html_route_cfg.get("match") or {})
        html_match_headers = list(html_match.get("headers") or [])
        html_match_headers.append(
            {
                "name": "accept",
                "safe_regex_match": {
                    "google_re2": {},
                    "regex": r"(?i).*text/html.*",
                },
            }
        )
        html_match["headers"] = html_match_headers
        html_route_cfg["match"] = html_match
        routes_by_host.setdefault(host_token, []).append(
            (html_primary_rank, html_route_cfg)
        )

    @staticmethod
    def _update_default_html_redirect(
        default_html_redirect_by_host: dict[str, str],
        host_token: str,
        path_prefix: str,
    ) -> None:
        """Pick a stable default-app path per host using slug priority.

        Hoisted into its own helper because the priority/fallback slug
        logic is the reason this block exists, and a named method makes
        that intent explicit.
        """
        slug = _path_prefix_app_slug(path_prefix)
        existing_default = default_html_redirect_by_host.get(host_token, "")
        existing_slug = _path_prefix_app_slug(existing_default)
        if not existing_default:
            default_html_redirect_by_host[host_token] = path_prefix
        elif slug == _PRIORITY_SLUG:
            default_html_redirect_by_host[host_token] = path_prefix
        elif slug == _FALLBACK_SLUG and existing_slug != _PRIORITY_SLUG:
            default_html_redirect_by_host[host_token] = path_prefix

    @staticmethod
    def _append_prefix_fallback_routes(
        *,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        path_prefix: str,
        cluster_name_val: str,
        strip_prefix: str,
        referer_fallback_rank: int,
        cookie_fallback_rank: int,
    ) -> None:
        """Emit referer + cookie fallback routes for prefix-bound services."""
        fb_regex_rewrite = fallback_regex_rewrite(
            path_prefix=path_prefix,
            strip_prefix=strip_prefix,
        )
        fallback_route = referer_fallback_route_cfg(
            host=host_token,
            path_prefix=path_prefix,
            cluster_name=cluster_name_val,
            regex_rewrite=fb_regex_rewrite,
        )
        routes_by_host.setdefault(host_token, []).append(
            (referer_fallback_rank, fallback_route)
        )
        cookie_fb_route = cookie_fallback_route_cfg(
            host=host_token,
            path_prefix=path_prefix,
            cluster_name=cluster_name_val,
            regex_rewrite=fb_regex_rewrite,
        )
        if cookie_fb_route:
            routes_by_host.setdefault(host_token, []).append(
                (cookie_fallback_rank, cookie_fb_route)
            )

    @classmethod
    def _append_default_html_fallback_routes(
        cls,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        default_html_redirect_by_host: dict[str, str],
    ) -> None:
        """Emit the per-host catch-all redirects to the default app path.

        This is a distinct pass from ``_collect_routes_from_routers``
        because it iterates over the aggregated defaults map, not the
        raw router list.
        """
        for host, default_path_prefix in default_html_redirect_by_host.items():
            host_token = str(host or "").strip().lower()
            path_prefix = str(default_path_prefix or "").strip()
            if not host_token or not path_prefix or path_prefix == "/":
                continue
            cls._append_default_redirects_for_host(
                routes_by_host, host_token, path_prefix,
            )

    @classmethod
    def _append_default_redirects_for_host(
        cls,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        path_prefix: str,
    ) -> None:
        """Emit the default-app fallback routes for a single host.

        Delegates each distinct fallback (HTML root redirect, app-root
        redirect, app-subpath redirect, exact-root redirect, non-HTML
        catch-all) to its own helper so the intent of each route is
        visible in the method name.
        """
        default_slug = _path_prefix_app_slug(path_prefix)
        default_cluster = f"service_{default_slug}"
        app_root = _path_prefix_root(path_prefix)
        homepage_path = f"{app_root}/{_DASHBOARD_SLUG}" if app_root and app_root != "/" else ""

        cls._append_html_root_redirect(routes_by_host, host_token, path_prefix)
        if app_root and app_root != "/":
            cls._append_app_root_redirects(
                routes_by_host, host_token, app_root, homepage_path,
            )
            cls._append_app_subpath_redirect(
                routes_by_host, host_token, app_root, homepage_path,
            )
        cls._append_exact_root_redirect(routes_by_host, host_token, path_prefix)
        cls._append_non_html_catchall(routes_by_host, host_token, default_cluster)

    @staticmethod
    def _append_html_root_redirect(
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        path_prefix: str,
    ) -> None:
        """HTML browsers at ``/`` get redirected to the default app path."""
        routes_by_host.setdefault(host_token, []).append(
            (
                _DEFAULT_HTML_REDIRECT_ROUTE_RANK,
                {
                    "match": {
                        "prefix": "/",
                        "headers": [html_accept_header_match()],
                    },
                    "redirect": {_KEY_PATH_REDIRECT: path_prefix},
                },
            )
        )

    @staticmethod
    def _append_app_root_redirects(
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        app_root: str,
        homepage_path: str,
    ) -> None:
        """``/app`` and ``/app/`` both redirect to the dashboard homepage path."""
        for bare_path in (app_root, f"{app_root}/"):
            routes_by_host.setdefault(host_token, []).append(
                (
                    _PRIMARY_ROUTE_RANK_BASE + len(bare_path) + 1,
                    {
                        "match": {"path": bare_path},
                        "redirect": {_KEY_PATH_REDIRECT: homepage_path},
                    },
                )
            )

    @staticmethod
    def _append_app_subpath_redirect(
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        app_root: str,
        homepage_path: str,
    ) -> None:
        """Catch unknown ``/app/X`` HTML requests and send them to the homepage."""
        routes_by_host.setdefault(host_token, []).append(
            (
                _DEFAULT_HTML_REDIRECT_ROUTE_RANK + 1,
                {
                    "match": {
                        "prefix": f"{app_root}/",
                        "headers": [html_accept_header_match()],
                    },
                    "redirect": {_KEY_PATH_REDIRECT: homepage_path},
                },
            )
        )

    @staticmethod
    def _append_exact_root_redirect(
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        path_prefix: str,
    ) -> None:
        """Exact ``/`` path (any client) → default app path.

        Ranked above cookie fallback so stale app cookies don't hijack
        the root URL for TV apps that follow redirects.
        """
        routes_by_host.setdefault(host_token, []).append(
            (
                _COOKIE_FALLBACK_ROUTE_RANK_BASE + 9999,
                {
                    "match": {"path": "/"},
                    "redirect": {_KEY_PATH_REDIRECT: path_prefix},
                },
            )
        )

    @staticmethod
    def _append_non_html_catchall(
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        host_token: str,
        default_cluster: str,
    ) -> None:
        """Non-HTML catch-all: proxy unknown paths to the default app cluster."""
        routes_by_host.setdefault(host_token, []).append(
            (
                _DEFAULT_HTML_REDIRECT_ROUTE_RANK - 1,
                {
                    "match": {"prefix": "/"},
                    "route": {
                        "cluster": default_cluster,
                        "timeout": "0s",
                    },
                },
            )
        )

    @staticmethod
    def _append_controller_path_aliases(
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
    ) -> None:
        """Add /app/controller → /app/media-stack-controller redirect aliases.

        Isolated from ``render`` because this is a fixed, service-specific
        shortcut that has nothing to do with the generic route pipeline.
        """
        for host_token, host_routes in routes_by_host.items():
            has_controller = any(
                r.get("match", {}).get("prefix") == "/app/media-stack-controller"
                for _, r in host_routes
                if isinstance(r, dict)
            )
            if has_controller:
                host_routes.append((
                    _PRIMARY_ROUTE_RANK_BASE + len("/app/controller") + 1,
                    {
                        "match": {"prefix": "/app/controller/"},
                        "redirect": {"prefix_rewrite": "/app/media-stack-controller/"},
                    },
                ))
                host_routes.append((
                    _PRIMARY_ROUTE_RANK_BASE + len("/app/controller") + 2,
                    {
                        "match": {"path": "/app/controller"},
                        "redirect": {_KEY_PATH_REDIRECT: "/app/media-stack-controller"},
                    },
                ))

    def _append_oidc_login_alias(
        self,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
    ) -> None:
        """Route root-level ``/login`` to Jellyseerr when gateway auth is on.

        Jellyseerr's OIDC flow redirects to ``/login`` at the origin root,
        which would otherwise 404. This helper is a no-op when ext_authz
        is disabled, mirroring the inline conditional that used to live
        in ``render``.
        """
        if not (self.auth_policy and self.auth_policy.ext_authz):
            return
        for host_token, host_routes in routes_by_host.items():
            has_jellyseerr = any(
                r.get("match", {}).get("prefix") == "/app/jellyseerr"
                for _, r in host_routes
                if isinstance(r, dict)
            )
            if has_jellyseerr:
                from media_stack.core.auth.envoy_ext_authz import route_ext_authz_disabled_config as _authz_off
                host_routes.append((
                    _PRIMARY_ROUTE_RANK_BASE + len("/login") + 1,
                    {
                        "match": {"prefix": "/login"},
                        "redirect": {"prefix_rewrite": "/app/jellyseerr/login"},
                        "typed_per_filter_config": _authz_off(),
                    },
                ))

    def _build_auth_portal_vhost(
        self,
        routes_by_host: dict[str, list[tuple[int, dict[str, Any]]]],
        clusters: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Create the auth-subdomain virtual host and register its upstream cluster.

        Returns None when gateway auth is inactive so the caller can skip
        the auth-specific insertion logic. Mutates ``routes_by_host`` and
        ``clusters`` in place to match the original inline behavior.
        """
        if not (self.auth_policy and self.auth_policy.ext_authz):
            return None
        ext = self.auth_policy.ext_authz
        # Derive auth portal hostname from the gateway host
        # (e.g. apps.media-stack.local → auth.media-stack.local)
        env = self.spec_resolver.compose_environment()
        gw_host = str(env.get("APP_GATEWAY_HOST", "")).strip()
        if not gw_host:
            return None
        parts = gw_host.split(".", 1)
        auth_host = f"auth.{parts[1]}" if len(parts) > 1 else f"auth.{gw_host}"
        auth_cluster_name = f"service_{ext.host}"
        # Proxy all requests on auth subdomain to auth provider
        auth_route: dict[str, Any] = {
            "match": {"prefix": "/"},
            "route": {"cluster": auth_cluster_name, "timeout": "0s"},
        }
        # Disable ext_authz on the auth portal itself
        from media_stack.core.auth.envoy_ext_authz import route_ext_authz_disabled_config
        auth_route["typed_per_filter_config"] = route_ext_authz_disabled_config()
        auth_vhost = {
            "name": f"vhost_{ext.host}",
            "domains": [auth_host, f"{auth_host}:*"],
            "routes": [auth_route],
        }
        # Remove any existing routes for this host to prevent
        # build_virtual_hosts from creating a duplicate vhost.
        routes_by_host.pop(auth_host, None)
        # Add cluster for auth provider (if not already in clusters)
        auth_cluster_names = {c["name"] for c in clusters}
        if auth_cluster_name not in auth_cluster_names:
            from media_stack.adapters.compose.edge.providers.envoy.clusters import build_cluster_entry
            clusters.append(build_cluster_entry(
                auth_cluster_name, address=ext.host, port=ext.port,
            ))
        return auth_vhost

    def _inject_tls_if_available(self, payload: dict[str, Any]) -> None:
        """Add TLS transport socket to the main listener.

        Compose deployments mount certs at /certs/. K8s uses ingress TLS
        termination so certs won't be present and Envoy runs plain HTTP.

        If a cert dir is mounted but empty on the compose path, we
        auto-mint a self-signed cert on the spot so the generator never
        silently produces a plain-HTTP listener (which was the root
        cause of a real apps-host TLS regression).
        """
        cert_path, key_path = self._resolve_or_mint_certs()
        if not cert_path:
            return

        listeners = payload.get("static_resources", {}).get("listeners", [])
        if not listeners:
            return
        main_listener = listeners[0]
        filter_chains = main_listener.get("filter_chains", [])
        if not filter_chains:
            return
        # Only add if not already present
        if "transport_socket" in filter_chains[0]:
            return
        filter_chains[0]["transport_socket"] = self._tls_transport_socket(
            cert_path, key_path,
        )
        main_listener["name"] = "listener_https"

        # HTTP→HTTPS redirect listener. Anyone who types
        # ``http://apps.media-stack.local`` (or omits the scheme and
        # the browser defaults to http) gets a 301 to the HTTPS URL.
        # Without this, every Authelia-protected route silently 404s
        # from the user's POV because OIDC requires HTTPS. Listens on
        # port 8080 (matched by the ``host:80→envoy:8080`` compose
        # mapping). (v1.0.147.)
        if not any(l.get("name") == "listener_http_redirect" for l in listeners):
            listeners.append(self._http_redirect_listener_config())

    @staticmethod
    def _tls_transport_socket(cert_path: str, key_path: str) -> dict[str, Any]:
        """Build the DownstreamTlsContext transport_socket payload.

        Extracted to keep ``_inject_tls_if_available`` focused on which
        branch to apply, not the verbose Envoy type URL for the socket
        config.
        """
        return {
            "name": "envoy.transport_sockets.tls",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext",
                "common_tls_context": {
                    "tls_certificates": [{
                        "certificate_chain": {"filename": cert_path},
                        "private_key": {"filename": key_path},
                    }],
                },
            },
        }

    @staticmethod
    def _http_redirect_listener_config() -> dict[str, Any]:
        """Build the plain-HTTP → HTTPS 301 redirect listener payload.

        Lives on its own because the nested Envoy filter-chain payload
        is long and purely declarative — splitting it out lets the TLS
        injection path be read as a one-liner append.
        """
        return {
            "name": "listener_http_redirect",
            "address": {
                "socket_address": {"address": "0.0.0.0", "port_value": 8080},
            },
            "filter_chains": [{
                "filters": [{
                    "name": "envoy.filters.network.http_connection_manager",
                    "typed_config": {
                        "@type": (
                            "type.googleapis.com/envoy.extensions.filters."
                            "network.http_connection_manager.v3.HttpConnectionManager"
                        ),
                        "stat_prefix": "ingress_http_redirect",
                        "codec_type": "AUTO",
                        "route_config": {
                            "name": "redirect_all",
                            "virtual_hosts": [{
                                "name": "redirect_all",
                                "domains": ["*"],
                                "routes": [{
                                    "match": {"prefix": "/"},
                                    "redirect": {
                                        "https_redirect": True,
                                        "response_code": "MOVED_PERMANENTLY",
                                    },
                                }],
                            }],
                        },
                        "http_filters": [{
                            "name": "envoy.filters.http.router",
                            "typed_config": {
                                "@type": (
                                    "type.googleapis.com/envoy.extensions."
                                    "filters.http.router.v3.Router"
                                ),
                            },
                        }],
                    },
                }],
            }],
        }

    _CERT_CANDIDATE_PATHS: tuple[tuple[str, str], ...] = (
        ("/certs/media-stack.crt", "/certs/media-stack.key"),
        ("/etc/envoy/certs/media-stack.crt",
         "/etc/envoy/certs/media-stack.key"),
    )

    def _resolve_or_mint_certs(self) -> tuple[str | None, str | None]:
        """Return (cert_path, key_path) for the compose Envoy listener.

        Resolution order:
          1. First cert+key pair that already exists on disk.
          2. If none exist but a writable candidate DIR is present,
             mint a self-signed pair into it and return that.
          3. Otherwise (K8s: no cert dir mounted), return (None, None)
             so the caller falls back to plain HTTP (K8s terminates
             TLS upstream).

        Logs every probe so regressions can be traced back to exactly
        which path failed and why — see the TLS-regression playbook.
        """
        log = logging.getLogger("media_stack.envoy.tls_resolve")
        for c, k in self._CERT_CANDIDATE_PATHS:
            cert_there = Path(c).exists()
            key_there = Path(k).exists()
            log.info("tls-probe pair=%s cert_exists=%s key_exists=%s",
                     c, cert_there, key_there)
            if cert_there and key_there:
                return c, k
        for c, k in self._CERT_CANDIDATE_PATHS:
            cert_dir = Path(c).parent
            dir_exists = cert_dir.exists()
            writable = dir_exists and os.access(cert_dir, os.W_OK)
            log.info("tls-mint dir=%s exists=%s writable=%s",
                     cert_dir, dir_exists, writable)
            if self._try_mint_cert(Path(c), Path(k)):
                log.info("tls-mint-ok pair=%s", c)
                return c, k
        log.error(
            "tls-resolve-fail tried=%s — generator will emit plain HTTP. "
            "On compose this silently breaks HTTPS on :443. Fix: mount "
            "the cert dir (:rw) into the calling container.",
            [c for c, _ in self._CERT_CANDIDATE_PATHS],
        )
        return None, None

    def _try_mint_cert(self, cert_file: Path, key_file: Path) -> bool:
        """Try to mint a self-signed cert at (cert_file, key_file).
        Returns True on success. Failures are logged at DEBUG — the
        caller will try the next candidate path."""
        cert_dir = cert_file.parent
        if not (cert_dir.exists() and os.access(cert_dir, os.W_OK)):
            return False
        try:
            svc = _TlsCertificateService(
                cert_dir=cert_dir, cert_name=cert_file.name,
                key_name=key_file.name,
            )
            svc.regenerate()
            return True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] compose cert mint failed at %s: %s", cert_dir, exc,
            )
            return False

    def _apply_auth_to_virtual_hosts(
        self,
        virtual_hosts: list[dict[str, Any]],
        service_map: dict[str, Any],
    ) -> None:
        """Apply per-route ext_authz bypass based on service auth policy.

        Walks all routes in all virtual hosts. For each route, extracts the
        cluster name to resolve the service, then applies auth policy.
        """
        if not self.auth_policy:
            return

        # Build cluster→service_name mapping
        cluster_to_service: dict[str, str] = {}
        for svc_name in service_map:
            cluster_to_service[_cluster_name(svc_name)] = svc_name

        for vhost in virtual_hosts:
            for route in vhost.get("routes", []):
                route_action = route.get("route") or {}
                cluster = route_action.get("cluster", "")
                svc_name = cluster_to_service.get(cluster, "")
                if svc_name:
                    self._apply_route_auth(route, svc_name)
