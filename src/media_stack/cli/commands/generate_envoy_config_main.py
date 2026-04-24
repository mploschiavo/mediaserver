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

        # Load bootstrap config edge hooks and profile YAML.
        bootstrap_config = os.environ.get("BOOTSTRAP_CONFIG_FILE", "")
        edge_hooks = _load_bootstrap_edge_hooks(bootstrap_config)
        profile = _load_profile(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
        # Merge dashboard-edited routing overrides on top of the profile.
        # The dashboard's POST /api/routing writes to
        # ``${CONFIG_ROOT}/.controller/routing-overrides.yaml`` because on
        # K8s the bootstrap profile is mounted from a read-only ConfigMap
        # and can't be edited in place. Without this merge, every
        # dashboard "Save Routing" silently no-ops on K8s — envoy-config
        # keeps generating with whatever the original profile said
        # (often the LAN defaults). Discovered v1.0.158 when the user's
        # iomio.io routing config wasn't applied even after re-saving.
        # Use a distinct local — ``config_root`` is reassigned to a Path
        # later in this function and reused at lines 227/298. Don't clobber.
        _override_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        overrides_path = Path(_override_root) / ".controller" / "routing-overrides.yaml"
        if overrides_path.is_file():
            try:
                import yaml as _yaml
                overrides = _yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
                ovr_routing = overrides.get("routing") or {}
                if ovr_routing:
                    profile.setdefault("routing", {}).update(ovr_routing)
                    print(f"[INFO] Applied routing overrides from {overrides_path}")
            except Exception as exc:
                print(f"[WARN] Failed to merge routing overrides: {exc}")
        routing = profile.get("routing") or {}

        # Routing config: profile YAML is the source of truth; env vars are fallback.
        route_strategy = routing.get("strategy") or os.environ.get("ROUTE_STRATEGY", "hybrid")
        base_domain = routing.get("base_domain") or "local"
        path_prefix = routing.get("app_path_prefix") or os.environ.get("APP_PATH_PREFIX", "/app")
        gateway_port = str(routing.get("gateway_port", "")) or os.environ.get("APP_GATEWAY_PORT", "")
        stack_name = str((profile.get("metadata") or {}).get("name", "")).strip()
        stack_subdomain = routing.get("stack_subdomain") or stack_name

        # Derive gateway_host from metadata.name + base_domain if not explicit.
        gateway_host = routing.get("gateway_host") or os.environ.get("APP_GATEWAY_HOST", "")
        if not gateway_host and route_strategy in ("hybrid", "path-prefix") and stack_subdomain:
            parts = [p for p in ["apps", stack_subdomain, base_domain] if p]
            gateway_host = ".".join(parts).lower()
        internet_exposed = bool(routing.get("internet_exposed")) or os.environ.get("INTERNET_EXPOSED", "0") == "1"
        media_server_direct_host = str((routing.get("direct_hosts") or {}).get("media_server", "")) or os.environ.get("MEDIA_SERVER_DIRECT_HOST", "")
        if not media_server_direct_host and stack_subdomain and base_domain:
            # Derive subdomain from the first media-category service in the registry.
            from media_stack.api.services.registry import SERVICES as _reg_services
            _ms_ids = [s.id for s in _reg_services if s.category == "media" and s.host]
            _ms_slug = _ms_ids[0] if _ms_ids else "media"
            parts = [p for p in [_ms_slug, stack_subdomain, base_domain] if p]
            media_server_direct_host = ".".join(parts).lower()
        auth_cfg = profile.get("auth") or {}
        auth_provider = str(auth_cfg.get("provider", "")) or os.environ.get("AUTH_PROVIDER", "")
        auth_middleware = str(auth_cfg.get("middleware", "")) or os.environ.get("AUTH_MIDDLEWARE", "")
        project_name = str((profile.get("metadata") or {}).get("name", "")) or os.environ.get("COMPOSE_PROJECT_NAME", "media-dev")

        # Service name lists — from env, config, or registry.
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

        # Load compose label specs from provider builtins, overlay config.json if present
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

        # Environment overrides for compose spec resolution.
        environment_overrides = {
            "APP_GATEWAY_HOST": gateway_host,
            "APP_PATH_PREFIX": path_prefix,
            "MEDIA_SERVER_DIRECT_HOST": media_server_direct_host,
        }
        if gateway_port:
            environment_overrides["APP_GATEWAY_PORT"] = gateway_port
            environment_overrides["EDGE_HTTP_PORT"] = gateway_port
            environment_overrides["TRAEFIK_HTTP_PORT"] = gateway_port

        # Import and instantiate the config generation pipeline.
        from media_stack.core.platforms.compose.services.spec import ComposeSpecResolver
        from media_stack.core.platforms.compose.services.labels import ComposeLabelConfig, ComposeLabelService
        from media_stack.core.platforms.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
        from media_stack.core.platforms.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
        from media_stack.core.platforms.compose.edge.providers.envoy.dynamic_config import (
            EnvoyDynamicConfigService,
        )

        # Router service names for envoy.
        router_service_names = ("envoy",)

        # In K8s mode, use a dummy compose file path (won't be read).
        spec_resolver = ComposeSpecResolver(
            compose_file=compose_file or Path("/dev/null"),
            compose_env_file=compose_env_file,
            compose_project_name=project_name,
            compose_profiles=(),
            selected_apps=(),
            edge_router_service_names=router_service_names,
            environment_overrides=environment_overrides,
        )

        label_service = ComposeLabelService(
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

        route_graph_service = ComposeEdgeRouteGraphService(
            label_service=label_service,
            spec_resolver=spec_resolver,
        )

        artifacts_dir = config_root / "envoy" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifacts_service = ComposeRuntimeArtifactService(
            runtime_artifacts_dir=artifacts_dir,
            info=lambda msg: print(f"[INFO] {msg}"),
        )

        # Resolve gateway auth policy from profile auth section.
        # Auth works on both LAN and internet-exposed deployments.
        auth_policy = None
        if auth_cfg.get("provider") and auth_cfg.get("provider") != "none":
            from media_stack.core.auth.gateway_policy import AuthContractService
            auth_contract = AuthContractService()
            # Build service list with categories for per-service resolution
            svc_list: list[tuple[str, str]] = []
            try:
                from media_stack.api.services.registry import SERVICES as _reg_svcs
                svc_list = [(s.id, s.category) for s in _reg_svcs]
            except Exception:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
            svc_list.append(("media-stack-controller", "infrastructure"))
            auth_policy = auth_contract.resolve_policy(auth_cfg, svc_list)
            if auth_policy.ext_authz:
                print(f"[INFO] Gateway auth: {auth_policy.mode} (ext_authz → {auth_policy.ext_authz.host}:{auth_policy.ext_authz.port})")
                protected = [s for s, p in auth_policy.service_policies.items() if p == "protected"]
                native = [s for s, p in auth_policy.service_policies.items() if p == "native"]
                print(f"[INFO]   Protected: {', '.join(sorted(protected)[:8])}{'...' if len(protected) > 8 else ''}")
                print(f"[INFO]   Native auth: {', '.join(sorted(native))}")

        dynamic_config_service = EnvoyDynamicConfigService(
            route_graph_service=route_graph_service,
            spec_resolver=spec_resolver,
            auth_policy=auth_policy,
        )

        # Load services — from compose spec or synthetic (K8s mode).
        if k8s_mode:
            print("[INFO] K8s mode: building synthetic services from known app list")
            services = _build_synthetic_services(
                gateway_host=gateway_host,
                compose_provider_specs=compose_provider_specs,
                strategy=route_strategy,
                stack_subdomain=stack_subdomain,
                base_domain=base_domain,
                path_prefix=path_prefix,
                media_server_direct_host=media_server_direct_host,
            )
            selected = dict(services)
        else:
            compose_spec = spec_resolver.load_compose_spec()
            services = dict(compose_spec.get("services") or {})
            selected = spec_resolver.selected_services(services)
        print(f"[INFO] Generating Envoy config for {len(selected)} services")

        # Render the Envoy config.
        render_result = dynamic_config_service.render(selected)
        payload = render_result.payload

        # Allow users to override the listener port via ENVOY_LISTENER_PORT.
        # Default is 8880 (non-privileged, works in Docker and K8s without root).
        listener_port = int(os.environ.get("ENVOY_LISTENER_PORT", "0"))
        if listener_port > 0:
            try:
                listeners = payload.get("static_resources", {}).get("listeners", [])
                if listeners:
                    addr = listeners[0].get("address", {}).get("socket_address", {})
                    addr["port_value"] = listener_port
            except Exception as exc:
                log_swallowed(exc)

        # Write output.
        envoy_dir = config_root / "envoy"
        envoy_dir.mkdir(parents=True, exist_ok=True)
        output_path = envoy_dir / "envoy.yaml"

        import yaml
        from media_stack.core.platforms.compose.edge.providers.envoy.tls_regression_guard import (
            TlsRegressionGuard,
        )

        # Refuse to silently downgrade the listener from TLS to plain
        # HTTP. See tls_regression_guard.py for the full story.
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
        return ports

    @staticmethod
    def _build_synthetic_services(
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

        Why this is rule-aware: before v1.0.159 this method emitted only
        ``Host(<svc>.local)`` for every service, regardless of the user's
        routing config. On K8s with hybrid strategy, that left envoy with
        zero routes for the user's actual gateway host — every
        ``https://<gateway>/app/<svc>/`` request 404'd because no vhost
        matched. The compose path sidesteps this because it reads real
        Traefik labels off live containers (where the rules are already
        spelled out).

        Strategy mapping (matches compose/services/labels.py contract):

            subdomain   → Host(<svc>.<stack_subdomain>.<base_domain>)
            path-prefix → Host(<gateway>) && PathPrefix(<prefix>/<svc>)
            hybrid      → BOTH rules emitted as separate routers, so a
                          user can hit either subdomain or gateway/path.

        For the media server we ALSO emit a direct-host rule when
        ``media_server_direct_host`` is set, matching the compose
        behavior — TV / mobile clients can hit ``jf.<base>`` directly
        without going through the gateway.
        """
        compose_provider_specs = compose_provider_specs or {}
        spec = compose_provider_specs.get("envoy") or compose_provider_specs.get("traefik") or {}
        enable_key = spec.get("enable_label_key", "traefik.enable")
        router_rule_tpl = spec.get("router_rule_key_template", "traefik.http.routers.{router_name}.rule")
        router_svc_tpl = spec.get("router_service_key_template", "traefik.http.routers.{router_name}.service")
        svc_port_tpl = spec.get("service_label_prefix", "traefik.http.services.")

        strategy = (strategy or "subdomain").lower().strip()
        wants_subdomain = strategy in ("subdomain", "hybrid")
        wants_path = strategy in ("path-prefix", "hybrid") and bool(gateway_host)

        # Build subdomain hostname pieces. When ``stack_subdomain`` is
        # set use ``<svc>.<stack_subdomain>.<base_domain>`` (matching the
        # compose path). When unset, fall back to the legacy
        # ``<svc>.local`` shape so existing LAN deployments stay happy.
        if stack_subdomain and base_domain:
            subdomain_parts = [stack_subdomain, base_domain]
        elif base_domain:
            subdomain_parts = [base_domain]
        else:
            subdomain_parts = ["local"]
        subdomain_suffix = ".".join(p for p in subdomain_parts if p)

        # The media-server slug needs special handling for the direct-host
        # rule — pull it from the registry so a stack that swapped Plex
        # in for Jellyfin still wires the right name.
        try:
            from media_stack.api.services.registry import SERVICES as _reg_services
            media_ids = {
                s.id for s in _reg_services if s.category == "media"
            }
        except Exception:
            media_ids = {"jellyfin", "plex", "emby"}

        services: dict[str, dict] = {}
        for svc_name, port in _DEFAULT_SERVICE_PORTS.items():
            labels: dict[str, str] = {
                enable_key: "true",
                f"{svc_port_tpl}{svc_name}.loadbalancer.server.port": str(port),
            }

            # Subdomain rule (if enabled by strategy). Two routers so
            # users hitting a LAN-only ``.local`` URL keep working even
            # after they reconfigure to a real domain — emit both when
            # ``base_domain`` differs from ``local``.
            if wants_subdomain:
                router_name = svc_name
                rule = f"Host(`{svc_name}.{subdomain_suffix}`)"
                labels[router_rule_tpl.replace("{router_name}", router_name)] = rule
                labels[router_svc_tpl.replace("{router_name}", router_name)] = svc_name
                # Always include the .local fallback so DNS-less LAN access
                # via /etc/hosts still works regardless of the user's
                # configured base_domain.
                if base_domain != "local":
                    fallback_router = f"{svc_name}-local"
                    fallback_rule = f"Host(`{svc_name}.local`)"
                    labels[router_rule_tpl.replace("{router_name}", fallback_router)] = fallback_rule
                    labels[router_svc_tpl.replace("{router_name}", fallback_router)] = svc_name

            # Gateway path-prefix rule. This is the one that was missing
            # entirely on K8s before v1.0.159 and caused
            # ``<gateway>/app/<svc>/`` to 404 against Envoy.
            if wants_path:
                router_name = f"{svc_name}-path"
                rule = f"Host(`{gateway_host}`) && PathPrefix(`{path_prefix}/{svc_name}`)"
                labels[router_rule_tpl.replace("{router_name}", router_name)] = rule
                labels[router_svc_tpl.replace("{router_name}", router_name)] = svc_name

            # Direct-host rule for the media server (one extra hostname
            # bypasses the gateway for TV / mobile clients).
            if media_server_direct_host and svc_name in media_ids:
                router_name = f"{svc_name}-direct"
                rule = f"Host(`{media_server_direct_host}`)"
                labels[router_rule_tpl.replace("{router_name}", router_name)] = rule
                labels[router_svc_tpl.replace("{router_name}", router_name)] = svc_name

            services[svc_name] = {
                "container_name": svc_name,
                "labels": labels,
            }
        return services


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
