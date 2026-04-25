#!/usr/bin/env python3
"""Generate Envoy runtime config from compose service definitions.

Standalone script that can run as a compose init service. Reads the compose
file, applies routing labels, and writes envoy.yaml to CONFIG_ROOT/envoy/.

Required env vars:
  COMPOSE_FILE          — path to docker-compose.yml
  CONFIG_ROOT           — base config directory (writes to CONFIG_ROOT/envoy/envoy.yaml)
  APP_GATEWAY_HOST      — gateway hostname (e.g. apps.media-dev.local)
  APP_PATH_PREFIX       — path prefix (e.g. /app)

Optional env vars:
  COMPOSE_ENV_FILE      — path to .env file for variable substitution
  COMPOSE_PROJECT_NAME  — compose project name
  ROUTE_STRATEGY        — subdomain|path-prefix|hybrid (default: hybrid)
  INTERNET_EXPOSED      — 0|1 (default: 0)
  MEDIA_SERVER_DIRECT_HOST — direct host for media server
  AUTH_PROVIDER          — auth provider name
  AUTH_MIDDLEWARE         — auth middleware name
  EDGE_PATH_PREFIX_PRESERVE — comma-separated service names to preserve prefix
  MEDIA_SERVER_SERVICES  — comma-separated media server service names
  APP_GATEWAY_PORT       — gateway port for env override
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import os
import sys
from pathlib import Path
import logging




# _DEFAULT_SERVICE_PORTS is initialized after the class definition (needs _build_default_service_ports)


class GenerateEnvoyConfigCommand:
    """Wraps Envoy config generation CLI entrypoint."""

    def main(self) -> None:
        paths = self._resolve_paths_or_exit()
        config_root = paths["config_root"]

        profile, edge_hooks = self._load_bootstrap_inputs()
        ctx = self._resolve_routing_context(profile, profile.get("routing") or {})
        preserve_names, media_server_names, redirect_names = (
            self._resolve_edge_service_name_lists(edge_hooks, profile)
        )
        compose_provider_specs = self._build_compose_provider_specs(edge_hooks)

        spec_resolver, route_graph_service = self._build_route_graph_pipeline(
            compose_file=paths["compose_file"],
            compose_env_file=paths["compose_env_file"],
            project_name=ctx["project_name"],
            environment_overrides=self._build_environment_overrides(
                gateway_host=ctx["gateway_host"],
                path_prefix=ctx["path_prefix"],
                media_server_direct_host=ctx["media_server_direct_host"],
                gateway_port=ctx["gateway_port"],
            ),
            route_strategy=ctx["route_strategy"],
            internet_exposed=ctx["internet_exposed"],
            gateway_host=ctx["gateway_host"],
            path_prefix=ctx["path_prefix"],
            media_server_direct_host=ctx["media_server_direct_host"],
            auth_provider=ctx["auth_provider"],
            auth_middleware=ctx["auth_middleware"],
            redirect_names=redirect_names,
            preserve_names=preserve_names,
            compose_provider_specs=compose_provider_specs,
            media_server_names=media_server_names,
            config_root=config_root,
        )
        dynamic_config_service = self._build_dynamic_config_service(
            route_graph_service, spec_resolver,
            self._resolve_gateway_auth_policy(ctx["auth_cfg"]),
        )

        self._render_and_persist(
            dynamic_config_service=dynamic_config_service,
            spec_resolver=spec_resolver,
            ctx=ctx,
            k8s_mode=paths["k8s_mode"],
            compose_provider_specs=compose_provider_specs,
            config_root=config_root,
        )

    def _render_and_persist(
        self,
        *,
        dynamic_config_service,
        spec_resolver,
        ctx: dict,
        k8s_mode: bool,
        compose_provider_specs: dict,
        config_root: Path,
    ) -> None:
        """Render the Envoy payload and write it to disk.

        Splits the back half of ``main`` into a single operation so the
        top-level entrypoint reads as "build pipeline → render + persist".
        """
        selected = self._load_services_for_render(
            k8s_mode=k8s_mode,
            spec_resolver=spec_resolver,
            gateway_host=ctx["gateway_host"],
            compose_provider_specs=compose_provider_specs,
            strategy=ctx["route_strategy"],
            stack_subdomain=ctx["stack_subdomain"],
            base_domain=ctx["base_domain"],
            path_prefix=ctx["path_prefix"],
            media_server_direct_host=ctx["media_server_direct_host"],
        )
        print(f"[INFO] Generating Envoy config for {len(selected)} services")

        render_result = dynamic_config_service.render(selected)
        payload = render_result.payload
        self._apply_listener_port_override(payload)

        envoy_dir = config_root / "envoy"
        envoy_dir.mkdir(parents=True, exist_ok=True)
        output_path = envoy_dir / "envoy.yaml"
        self._write_envoy_yaml(output_path, payload, render_result)

    @classmethod
    def _load_bootstrap_inputs(cls) -> tuple[dict, dict]:
        """Load the bootstrap profile + edge-hook config and apply overrides.

        Consolidates two related reads (profile YAML + config JSON) so
        ``main`` sees one call where previously it drove three steps.
        """
        bootstrap_config = os.environ.get("BOOTSTRAP_CONFIG_FILE", "")
        edge_hooks = _load_bootstrap_edge_hooks(bootstrap_config)
        profile = _load_profile(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
        cls._apply_dashboard_routing_overrides(profile)
        return profile, edge_hooks

    @staticmethod
    def _resolve_paths_or_exit() -> dict:
        """Resolve the compose/config-root paths and the K8s-mode flag.

        Exits the process with code 1 if ``CONFIG_ROOT`` is missing, so
        the rest of ``main`` can treat the return as always-valid.
        """
        compose_file_str = os.environ.get("COMPOSE_FILE", "")
        config_root_str = os.environ.get("CONFIG_ROOT", "")
        if not config_root_str:
            print("ERROR: CONFIG_ROOT env var is required", file=sys.stderr)
            sys.exit(1)
        compose_file = Path(compose_file_str) if compose_file_str and compose_file_str != "/dev/null" else None
        config_root = Path(config_root_str)
        k8s_mode = compose_file is None or not compose_file.exists()
        compose_env_file_str = os.environ.get("COMPOSE_ENV_FILE", "")
        compose_env_file = Path(compose_env_file_str) if compose_env_file_str else None
        return {
            "compose_file": compose_file,
            "compose_env_file": compose_env_file,
            "config_root": config_root,
            "k8s_mode": k8s_mode,
        }

    @staticmethod
    def _build_dynamic_config_service(route_graph_service, spec_resolver, auth_policy):
        """Wire up EnvoyDynamicConfigService so ``main`` doesn't import it directly.

        Keeps the lazy-import for the envoy renderer co-located with
        its single construction site.
        """
        from media_stack.core.platforms.compose.edge.providers.envoy.dynamic_config import (
            EnvoyDynamicConfigService,
        )
        return EnvoyDynamicConfigService(
            route_graph_service=route_graph_service,
            spec_resolver=spec_resolver,
            auth_policy=auth_policy,
        )

    def _log_tls_regression(self, output_path: Path) -> None:
        """Emit a loud, actionable error when a write would silently
        lose TLS. Captures caller context so ops can trace the bug to
        a specific container/code path."""
        import socket
        logging.getLogger("media_stack.envoy.tls_guard").error(
            "Refusing to write %s: existing config has TLS but new "
            "config does not. host=%s pid=%d caller_container=%s "
            "certs_mount_exists=%s cert_files=%s. Fix the cert-mount "
            "in the container running this generator, or set "
            "ENVOY_FORCE_PLAIN_HTTP=1 for a deliberate downgrade.",
            output_path, socket.gethostname(), os.getpid(),
            os.getenv("HOSTNAME", "?"),
            Path("/certs").is_dir(), sorted(
                p.name for p in Path("/certs").glob("*")
            ) if Path("/certs").is_dir() else [],
        )

    @staticmethod
    def _apply_dashboard_routing_overrides(profile: dict) -> None:
        """Merge dashboard-edited routing overrides on top of the profile.

        The dashboard's POST /api/routing writes to
        ``${CONFIG_ROOT}/.controller/routing-overrides.yaml`` because on
        K8s the bootstrap profile is mounted from a read-only ConfigMap
        and can't be edited in place. Without this merge, every dashboard
        "Save Routing" silently no-ops on K8s — envoy-config keeps
        generating with whatever the original profile said (often the LAN
        defaults). Discovered v1.0.158 when iomio.io routing wasn't
        applied even after re-saving.
        """
        override_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        overrides_path = Path(override_root) / ".controller" / "routing-overrides.yaml"
        if not overrides_path.is_file():
            return
        try:
            import yaml as _yaml
            overrides = _yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
            ovr_routing = overrides.get("routing") or {}
            if ovr_routing:
                profile.setdefault("routing", {}).update(ovr_routing)
                print(f"[INFO] Applied routing overrides from {overrides_path}")
        except Exception as exc:
            print(f"[WARN] Failed to merge routing overrides: {exc}")

    @staticmethod
    def _resolve_routing_context(profile: dict, routing: dict) -> dict:
        """Resolve the full routing context (strategy, hosts, auth, etc).

        Each field prefers the profile YAML value and falls back to the
        matching env var so that YAML remains the source of truth but
        legacy env-var driven deployments still work.
        """
        route_strategy = routing.get("strategy") or os.environ.get("ROUTE_STRATEGY", "hybrid")
        base_domain = routing.get("base_domain") or "local"
        path_prefix = routing.get("app_path_prefix") or os.environ.get("APP_PATH_PREFIX", "/app")
        gateway_port = str(routing.get("gateway_port", "")) or os.environ.get("APP_GATEWAY_PORT", "")
        stack_name = str((profile.get("metadata") or {}).get("name", "")).strip()
        stack_subdomain = routing.get("stack_subdomain") or stack_name

        gateway_host = routing.get("gateway_host") or os.environ.get("APP_GATEWAY_HOST", "")
        if not gateway_host and route_strategy in ("hybrid", "path-prefix") and stack_subdomain:
            parts = [p for p in ["apps", stack_subdomain, base_domain] if p]
            gateway_host = ".".join(parts).lower()
        internet_exposed = bool(routing.get("internet_exposed")) or os.environ.get("INTERNET_EXPOSED", "0") == "1"
        media_server_direct_host = str((routing.get("direct_hosts") or {}).get("media_server", "")) or os.environ.get("MEDIA_SERVER_DIRECT_HOST", "")
        if not media_server_direct_host and stack_subdomain and base_domain:
            from media_stack.api.services.registry import SERVICES as _reg_services
            _ms_ids = [s.id for s in _reg_services if s.category == "media" and s.host]
            _ms_slug = _ms_ids[0] if _ms_ids else "media"
            parts = [p for p in [_ms_slug, stack_subdomain, base_domain] if p]
            media_server_direct_host = ".".join(parts).lower()
        auth_cfg = profile.get("auth") or {}
        return {
            "route_strategy": route_strategy,
            "base_domain": base_domain,
            "path_prefix": path_prefix,
            "gateway_port": gateway_port,
            "stack_subdomain": stack_subdomain,
            "gateway_host": gateway_host,
            "internet_exposed": internet_exposed,
            "media_server_direct_host": media_server_direct_host,
            "auth_cfg": auth_cfg,
            "auth_provider": str(auth_cfg.get("provider", "")) or os.environ.get("AUTH_PROVIDER", ""),
            "auth_middleware": str(auth_cfg.get("middleware", "")) or os.environ.get("AUTH_MIDDLEWARE", ""),
            "project_name": str((profile.get("metadata") or {}).get("name", "")) or os.environ.get("COMPOSE_PROJECT_NAME", "media-dev"),
        }

    @classmethod
    def _resolve_edge_service_name_lists(
        cls, edge_hooks: dict, profile: dict,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Resolve (preserve, media_server, redirect) service-name tuples.

        Each list is resolved with the same fall-back chain (env → config
        → registry) — extracting the helper lets the caller treat all
        three as a single step.
        """
        preserve_names = _csv(os.environ.get("EDGE_PATH_PREFIX_PRESERVE", ""))
        if not preserve_names:
            by_provider = edge_hooks.get("path_prefix_preserve_service_names_by_provider", {})
            preserve_names = tuple(
                str(s).strip().lower()
                for s in (by_provider.get("envoy") or [])
                if str(s).strip()
            )
        if not preserve_names:
            try:
                from media_stack.api.services.registry import get_preserve_path_prefix_services
                preserve_names = tuple(s.id for s in get_preserve_path_prefix_services())
            except Exception as exc:
                log_swallowed(exc)

        media_server_names = _csv(os.environ.get("MEDIA_SERVER_SERVICES", ""))
        if not media_server_names:
            media_server_names = tuple(
                str(s).strip().lower()
                for s in (edge_hooks.get("media_server_service_names") or [])
                if str(s).strip()
            )
        if not media_server_names:
            raw_profile = (profile.get("routing") or {}).get("media_server_service_names")
            if isinstance(raw_profile, list) and raw_profile:
                media_server_names = tuple(str(s).strip().lower() for s in raw_profile if str(s).strip())

        redirect_names = _csv(os.environ.get("EDGE_PATH_PREFIX_REDIRECT", ""))
        if not redirect_names:
            by_provider = edge_hooks.get("path_prefix_redirect_service_names_by_provider", {})
            redirect_names = tuple(
                str(s).strip().lower()
                for s in (by_provider.get("envoy") or [])
                if str(s).strip()
            )
        if not redirect_names:
            try:
                from media_stack.api.services.registry import get_web_ui_services
                redirect_names = tuple(s.id for s in get_web_ui_services())
            except Exception as exc:
                log_swallowed(exc)
        return preserve_names, media_server_names, redirect_names

    @staticmethod
    def _resolve_gateway_auth_policy(auth_cfg: dict):
        """Return a GatewayAuthPolicy when the profile declares an auth provider.

        Returning None when auth is disabled lets ``main`` treat the
        policy as an opaque value and keeps the provider/contract plumbing
        out of the entrypoint body.
        """
        if not (auth_cfg.get("provider") and auth_cfg.get("provider") != "none"):
            return None
        from media_stack.core.auth.gateway_policy import AuthContractService
        auth_contract = AuthContractService()
        svc_list: list[tuple[str, str]] = []
        try:
            from media_stack.api.services.registry import SERVICES as _reg_svcs
            svc_list = [(s.id, s.category) for s in _reg_svcs]
        except Exception:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        svc_list.append(("media-stack-controller", "infrastructure"))
        svc_list.append(("media-stack-ui", "infrastructure"))
        auth_policy = auth_contract.resolve_policy(auth_cfg, svc_list)
        if auth_policy.ext_authz:
            print(f"[INFO] Gateway auth: {auth_policy.mode} (ext_authz → {auth_policy.ext_authz.host}:{auth_policy.ext_authz.port})")
            protected = [s for s, p in auth_policy.service_policies.items() if p == "protected"]
            native = [s for s, p in auth_policy.service_policies.items() if p == "native"]
            print(f"[INFO]   Protected: {', '.join(sorted(protected)[:8])}{'...' if len(protected) > 8 else ''}")
            print(f"[INFO]   Native auth: {', '.join(sorted(native))}")
        return auth_policy

    @staticmethod
    def _load_services_for_render(
        *,
        k8s_mode: bool,
        spec_resolver,
        gateway_host: str,
        compose_provider_specs: dict,
        strategy: str,
        stack_subdomain: str,
        base_domain: str,
        path_prefix: str,
        media_server_direct_host: str,
    ) -> dict[str, dict]:
        """Return the service map to feed into the dynamic-config renderer.

        Synthesizes a service map in K8s mode (no compose file); otherwise
        applies the resolver's selected-services filter.
        """
        if k8s_mode:
            print("[INFO] K8s mode: building synthetic services from known app list")
            services = _build_synthetic_services(
                gateway_host=gateway_host,
                compose_provider_specs=compose_provider_specs,
                strategy=strategy,
                stack_subdomain=stack_subdomain,
                base_domain=base_domain,
                path_prefix=path_prefix,
                media_server_direct_host=media_server_direct_host,
            )
            return dict(services)
        compose_spec = spec_resolver.load_compose_spec()
        services = dict(compose_spec.get("services") or {})
        return spec_resolver.selected_services(services)

    @staticmethod
    def _apply_listener_port_override(payload: dict) -> None:
        """Override listener port via ENVOY_LISTENER_PORT when non-zero.

        The default port (8880) is non-privileged so it works in both
        Docker and K8s without root; operators can override when they
        terminate TLS elsewhere.
        """
        listener_port = int(os.environ.get("ENVOY_LISTENER_PORT", "0"))
        if listener_port <= 0:
            return
        try:
            listeners = payload.get("static_resources", {}).get("listeners", [])
            if listeners:
                addr = listeners[0].get("address", {}).get("socket_address", {})
                addr["port_value"] = listener_port
        except Exception as exc:
            log_swallowed(exc)

    @staticmethod
    def _build_compose_provider_specs(edge_hooks: dict) -> dict:
        """Merge builtin compose label specs with edge-hook overrides.

        Kept separate so ``main`` does not own the shape of the provider
        registry or how per-provider spec overrides are layered.
        """
        from media_stack.core.edge.provider_registry import compose_label_specs_by_provider
        compose_provider_specs: dict = {
            p: dict(s) for p, s in compose_label_specs_by_provider().items()
        }
        raw_specs = edge_hooks.get("compose_provider_specs", {})
        if isinstance(raw_specs, dict):
            for provider_key, spec in raw_specs.items():
                if isinstance(spec, dict) and spec:
                    merged = dict(compose_provider_specs.get(provider_key) or {})
                    merged.update(spec)
                    compose_provider_specs[provider_key] = merged
        return compose_provider_specs

    @staticmethod
    def _build_environment_overrides(
        *, gateway_host: str, path_prefix: str,
        media_server_direct_host: str, gateway_port: str,
    ) -> dict[str, str]:
        """Collect env-var overrides the compose resolver needs to see.

        Pulled out so the (small, mechanical) dictionary construction
        doesn't bulk up the entrypoint body.
        """
        environment_overrides = {
            "APP_GATEWAY_HOST": gateway_host,
            "APP_PATH_PREFIX": path_prefix,
            "MEDIA_SERVER_DIRECT_HOST": media_server_direct_host,
        }
        if gateway_port:
            environment_overrides["APP_GATEWAY_PORT"] = gateway_port
            environment_overrides["EDGE_HTTP_PORT"] = gateway_port
            environment_overrides["TRAEFIK_HTTP_PORT"] = gateway_port
        return environment_overrides

    @classmethod
    def _build_route_graph_pipeline(
        cls, *, compose_file: Path | None, compose_env_file: Path | None,
        project_name: str, environment_overrides: dict[str, str],
        route_strategy: str, internet_exposed: bool, gateway_host: str,
        path_prefix: str, media_server_direct_host: str, auth_provider: str,
        auth_middleware: str, redirect_names: tuple[str, ...],
        preserve_names: tuple[str, ...], compose_provider_specs: dict,
        media_server_names: tuple[str, ...], config_root: Path,
    ):
        """Instantiate ComposeSpecResolver + label + route-graph + artifacts stack.

        Packaged as one helper because these services are always
        constructed together and always with the same wiring, so the
        caller only needs the inputs and the pipeline outputs.
        """
        spec_resolver = cls._new_compose_spec_resolver(
            compose_file=compose_file, compose_env_file=compose_env_file,
            project_name=project_name, environment_overrides=environment_overrides,
        )
        label_service = cls._new_compose_label_service(
            project_name=project_name, route_strategy=route_strategy,
            internet_exposed=internet_exposed, gateway_host=gateway_host,
            path_prefix=path_prefix,
            media_server_direct_host=media_server_direct_host,
            auth_provider=auth_provider, auth_middleware=auth_middleware,
            redirect_names=redirect_names, preserve_names=preserve_names,
            compose_provider_specs=compose_provider_specs,
            media_server_names=media_server_names,
        )
        from media_stack.core.platforms.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
        route_graph_service = ComposeEdgeRouteGraphService(
            label_service=label_service, spec_resolver=spec_resolver,
        )
        cls._ensure_artifacts_dir(config_root)
        return spec_resolver, route_graph_service

    @staticmethod
    def _new_compose_spec_resolver(
        *,
        compose_file: Path | None,
        compose_env_file: Path | None,
        project_name: str,
        environment_overrides: dict[str, str],
    ):
        """Build the ComposeSpecResolver, using ``/dev/null`` in K8s mode.

        Isolates the import + constructor so
        ``_build_route_graph_pipeline`` reads as three one-liner calls.
        """
        from media_stack.core.platforms.compose.services.spec import ComposeSpecResolver
        return ComposeSpecResolver(
            compose_file=compose_file or Path("/dev/null"),
            compose_env_file=compose_env_file,
            compose_project_name=project_name,
            compose_profiles=(),
            selected_apps=(),
            edge_router_service_names=("envoy",),
            environment_overrides=environment_overrides,
        )

    @staticmethod
    def _new_compose_label_service(
        *,
        project_name: str,
        route_strategy: str,
        internet_exposed: bool,
        gateway_host: str,
        path_prefix: str,
        media_server_direct_host: str,
        auth_provider: str,
        auth_middleware: str,
        redirect_names: tuple[str, ...],
        preserve_names: tuple[str, ...],
        compose_provider_specs: dict,
        media_server_names: tuple[str, ...],
    ):
        """Build the ComposeLabelService wired for the ``envoy`` edge router."""
        from media_stack.core.platforms.compose.services.labels import ComposeLabelConfig, ComposeLabelService
        return ComposeLabelService(
            cfg=ComposeLabelConfig(
                project_name=project_name,
                edge_router_provider="envoy",
                route_strategy=route_strategy,
                internet_exposed=internet_exposed,
                app_gateway_host=gateway_host,
                app_path_prefix=path_prefix,
                media_server_direct_host=media_server_direct_host,
                auth_provider=auth_provider,
                auth_middleware=auth_middleware,
                path_prefix_redirect_service_names=redirect_names,
                path_prefix_preserve_service_names=preserve_names,
                edge_compose_provider_specs=compose_provider_specs,
                auth_provider_middleware_defaults={},
                media_server_service_names=media_server_names,
            )
        )

    @staticmethod
    def _ensure_artifacts_dir(config_root: Path) -> None:
        """Create the envoy artifacts dir and instantiate the runtime-artifacts
        service for its side-effect of recording metadata."""
        from media_stack.core.platforms.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
        artifacts_dir = config_root / "envoy" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        ComposeRuntimeArtifactService(
            runtime_artifacts_dir=artifacts_dir,
            info=lambda msg: print(f"[INFO] {msg}"),
        )

    def _write_envoy_yaml(self, output_path: Path, payload: dict, render_result) -> None:
        """Serialize the Envoy config to disk, guarding against TLS downgrade.

        The TLS regression guard lives here (instead of inline) so the
        main pipeline can be read as a sequence of resolve-build-render-
        write steps without inlining a safety net.
        """
        import yaml
        from media_stack.core.platforms.compose.edge.providers.envoy.tls_regression_guard import (
            TlsRegressionGuard,
        )
        if TlsRegressionGuard().would_lose_tls(output_path, payload):
            if (os.getenv("ENVOY_FORCE_PLAIN_HTTP", "") or "").strip() != "1":
                self._log_tls_regression(output_path)
                sys.exit(2)
        with open(output_path, "w") as f:
            yaml.dump(payload, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(
            f"[OK] Envoy config written to {output_path} "
            f"(routes={render_result.route_count}, clusters={render_result.cluster_count})"
        )


    @staticmethod
    def _csv(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @staticmethod
    def _load_bootstrap_edge_hooks(config_file: str | None) -> dict:
        """Load edge hooks from bootstrap config JSON if available."""
        if not config_file:
            return {}
        path = Path(config_file)
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                cfg = json.load(f)
            return cfg.get("adapter_hooks", {}).get("edge", {})
        except Exception:
            return {}

    @staticmethod
    def _load_profile(profile_file: str | None) -> dict:
        """Load the bootstrap profile YAML — single source of truth for routing config."""
        if not profile_file:
            return {}
        path = Path(profile_file)
        if not path.exists():
            return {}
        try:
            import yaml
    
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    @staticmethod
    def _build_default_service_ports() -> dict[str, int]:
        from media_stack.api.services.registry import SERVICES
        ports = {s.id: s.port for s in SERVICES if s.port > 0}
        # Non-registry services that still need Envoy routing entries.
        ports.setdefault("media-stack-controller", 9100)
        # UI container — nginx serves dashboard + reverse-proxies /api
        # to the controller. Port matches both compose (containerPort
        # 8080) and the k8s Service (port 8080 → targetPort 8080) so
        # the same upstream URI works in both deployments.
        ports.setdefault("media-stack-ui", 8080)
        return ports

    @classmethod
    def _build_synthetic_services(
        cls,
        gateway_host: str = "",
        compose_provider_specs: dict | None = None,
        strategy: str = "subdomain",
        stack_subdomain: str = "",
        base_domain: str = "local",
        path_prefix: str = "/app",
        media_server_direct_host: str = "",
    ) -> dict[str, dict]:
        """Build compose-compatible service dicts from known services.

        Used when no compose file is available (K8s mode). Generates the
        same label structure that the compose label service expects so the
        downstream route-graph + envoy-config pipeline runs unchanged.

        See ``_append_router_labels_for_service`` for the per-service
        rule emission, which encapsulates the strategy mapping
        (subdomain / path-prefix / hybrid) plus direct-host overlay.
        """
        spec = (compose_provider_specs or {}).get("envoy") or (compose_provider_specs or {}).get("traefik") or {}
        label_templates = {
            "enable_key": spec.get("enable_label_key", "traefik.enable"),
            "router_rule_tpl": spec.get("router_rule_key_template", "traefik.http.routers.{router_name}.rule"),
            "router_svc_tpl": spec.get("router_service_key_template", "traefik.http.routers.{router_name}.service"),
            "svc_port_tpl": spec.get("service_label_prefix", "traefik.http.services."),
        }
        strategy = (strategy or "subdomain").lower().strip()
        subdomain_suffix = cls._subdomain_suffix_for(stack_subdomain, base_domain)
        media_ids = cls._resolve_media_service_ids()

        services: dict[str, dict] = {}
        for svc_name, port in _DEFAULT_SERVICE_PORTS.items():
            labels: dict[str, str] = {
                label_templates["enable_key"]: "true",
                f"{label_templates['svc_port_tpl']}{svc_name}.loadbalancer.server.port": str(port),
            }
            cls._append_router_labels_for_service(
                labels=labels,
                svc_name=svc_name,
                strategy=strategy,
                gateway_host=gateway_host,
                path_prefix=path_prefix,
                subdomain_suffix=subdomain_suffix,
                base_domain=base_domain,
                media_ids=media_ids,
                media_server_direct_host=media_server_direct_host,
                label_templates=label_templates,
            )
            services[svc_name] = {"container_name": svc_name, "labels": labels}
        return services

    @staticmethod
    def _subdomain_suffix_for(stack_subdomain: str, base_domain: str) -> str:
        """Pick ``<stack>.<base>``, ``<base>``, or ``local`` as the suffix.

        Mirrors the compose path's precedence so LAN-only deployments
        keep working when ``stack_subdomain`` is empty.
        """
        if stack_subdomain and base_domain:
            parts = [stack_subdomain, base_domain]
        elif base_domain:
            parts = [base_domain]
        else:
            parts = ["local"]
        return ".".join(p for p in parts if p)

    @staticmethod
    def _resolve_media_service_ids() -> set[str]:
        """Return media-category service IDs from the registry (best-effort).

        Falls back to the static set when the registry can't be imported
        so synthetic-service generation never crashes on reduced-import
        deployments.
        """
        try:
            from media_stack.api.services.registry import SERVICES as _reg_services
            return {s.id for s in _reg_services if s.category == "media"}
        except Exception:
            return {"jellyfin", "plex", "emby"}

    @staticmethod
    def _append_router_labels_for_service(
        *,
        labels: dict[str, str],
        svc_name: str,
        strategy: str,
        gateway_host: str,
        path_prefix: str,
        subdomain_suffix: str,
        base_domain: str,
        media_ids: set[str],
        media_server_direct_host: str,
        label_templates: dict[str, str],
    ) -> None:
        """Emit router rule/service labels for one service per strategy.

        Consolidates the three rule families (subdomain, path-prefix,
        direct-host) so strategy decisions live in one place and the
        main builder loop only has to iterate the service map.
        """
        rule_tpl = label_templates["router_rule_tpl"]
        svc_tpl = label_templates["router_svc_tpl"]
        wants_subdomain = strategy in ("subdomain", "hybrid")
        wants_path = strategy in ("path-prefix", "hybrid") and bool(gateway_host)

        if wants_subdomain:
            labels[rule_tpl.replace("{router_name}", svc_name)] = f"Host(`{svc_name}.{subdomain_suffix}`)"
            labels[svc_tpl.replace("{router_name}", svc_name)] = svc_name
            # Always include the .local fallback so DNS-less LAN access
            # via /etc/hosts still works regardless of the user's
            # configured base_domain.
            if base_domain != "local":
                fb = f"{svc_name}-local"
                labels[rule_tpl.replace("{router_name}", fb)] = f"Host(`{svc_name}.local`)"
                labels[svc_tpl.replace("{router_name}", fb)] = svc_name
        if wants_path:
            rn = f"{svc_name}-path"
            labels[rule_tpl.replace("{router_name}", rn)] = (
                f"Host(`{gateway_host}`) && PathPrefix(`{path_prefix}/{svc_name}`)"
            )
            labels[svc_tpl.replace("{router_name}", rn)] = svc_name
        if media_server_direct_host and svc_name in media_ids:
            rn = f"{svc_name}-direct"
            labels[rule_tpl.replace("{router_name}", rn)] = f"Host(`{media_server_direct_host}`)"
            labels[svc_tpl.replace("{router_name}", rn)] = svc_name


_instance = GenerateEnvoyConfigCommand()
main = _instance.main
_csv = _instance._csv
_load_bootstrap_edge_hooks = _instance._load_bootstrap_edge_hooks
_load_profile = _instance._load_profile
_build_default_service_ports = _instance._build_default_service_ports
_build_synthetic_services = _instance._build_synthetic_services

# Known services with their default ports — used when compose file is unavailable (K8s).
_DEFAULT_SERVICE_PORTS: dict[str, int] = _build_default_service_ports()

if __name__ == "__main__":
    main()
