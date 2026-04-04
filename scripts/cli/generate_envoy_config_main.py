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

import json
import os
import sys
from pathlib import Path


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


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


def main() -> None:
    compose_file_str = os.environ.get("COMPOSE_FILE", "")
    config_root_str = os.environ.get("CONFIG_ROOT", "")
    if not compose_file_str or not config_root_str:
        print("ERROR: COMPOSE_FILE and CONFIG_ROOT env vars are required", file=sys.stderr)
        sys.exit(1)

    compose_file = Path(compose_file_str)
    config_root = Path(config_root_str)
    if not compose_file.exists():
        print(f"ERROR: COMPOSE_FILE not found: {compose_file}", file=sys.stderr)
        sys.exit(1)

    compose_env_file_str = os.environ.get("COMPOSE_ENV_FILE", "")
    compose_env_file = Path(compose_env_file_str) if compose_env_file_str else None

    # Load edge hooks from bootstrap config if available.
    bootstrap_config = os.environ.get("BOOTSTRAP_CONFIG_FILE", "")
    edge_hooks = _load_bootstrap_edge_hooks(bootstrap_config)

    gateway_host = os.environ.get("APP_GATEWAY_HOST", "")
    gateway_port = os.environ.get("APP_GATEWAY_PORT", "")
    path_prefix = os.environ.get("APP_PATH_PREFIX", "/app")
    route_strategy = os.environ.get("ROUTE_STRATEGY", "hybrid")
    internet_exposed = os.environ.get("INTERNET_EXPOSED", "0") == "1"
    media_server_direct_host = os.environ.get("MEDIA_SERVER_DIRECT_HOST", "")
    auth_provider = os.environ.get("AUTH_PROVIDER", "")
    auth_middleware = os.environ.get("AUTH_MIDDLEWARE", "")
    project_name = os.environ.get("COMPOSE_PROJECT_NAME", "media-dev")

    # Service name lists — from env or bootstrap config edge hooks.
    preserve_names = _csv(os.environ.get("EDGE_PATH_PREFIX_PRESERVE", ""))
    if not preserve_names:
        by_provider = edge_hooks.get("path_prefix_preserve_service_names_by_provider", {})
        preserve_names = tuple(
            str(s).strip().lower()
            for s in (by_provider.get("envoy") or [])
            if str(s).strip()
        )

    media_server_names = _csv(os.environ.get("MEDIA_SERVER_SERVICES", ""))
    if not media_server_names:
        media_server_names = tuple(
            str(s).strip().lower()
            for s in (edge_hooks.get("media_server_service_names") or [])
            if str(s).strip()
        )

    redirect_names = _csv(os.environ.get("EDGE_PATH_PREFIX_REDIRECT", ""))
    if not redirect_names:
        by_provider = edge_hooks.get("path_prefix_redirect_service_names_by_provider", {})
        redirect_names = tuple(
            str(s).strip().lower()
            for s in (by_provider.get("envoy") or [])
            if str(s).strip()
        )

    compose_provider_specs: dict = {}
    raw_specs = edge_hooks.get("compose_provider_specs", {})
    if isinstance(raw_specs, dict):
        envoy_spec = raw_specs.get("envoy", {})
        if isinstance(envoy_spec, dict):
            compose_provider_specs = {"envoy": envoy_spec}

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
    from core.platforms.compose.services.spec import ComposeSpecResolver
    from core.platforms.compose.services.labels import ComposeLabelConfig, ComposeLabelService
    from core.platforms.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
    from core.platforms.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
    from core.platforms.compose.edge.providers.envoy.dynamic_config import (
        EnvoyDynamicConfigService,
    )

    # Router service names for envoy.
    router_service_names = ("envoy",)

    spec_resolver = ComposeSpecResolver(
        compose_file=compose_file,
        compose_env_file=compose_env_file,
        compose_project_name=project_name,
        compose_profiles=(),
        selected_apps=(),
        edge_router_service_names=router_service_names,
        environment_overrides=environment_overrides,
    )

    label_service = ComposeLabelService(
        cfg=ComposeLabelConfig(
            edge_router_provider="envoy",
            route_strategy=route_strategy,
            internet_exposed=internet_exposed,
            app_gateway_host=gateway_host,
            app_path_prefix=path_prefix,
            media_server_direct_host=media_server_direct_host,
            auth_provider=auth_provider,
            auth_middleware=auth_middleware,
            edge_path_prefix_redirect_service_names=redirect_names,
            edge_path_prefix_preserve_service_names=preserve_names,
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
        artifacts_dir=artifacts_dir,
        info=lambda msg: print(f"[INFO] {msg}"),
    )

    dynamic_config_service = EnvoyDynamicConfigService(
        route_graph_service=route_graph_service,
        spec_resolver=spec_resolver,
    )

    # Load services from compose spec.
    compose_spec = spec_resolver.load_compose_spec()
    services = dict(compose_spec.get("services") or {})

    # Select services (exclude non-selected profiles, include router services).
    selected = spec_resolver.selected_services(services)
    print(f"[INFO] Generating Envoy config for {len(selected)} services")

    # Render the Envoy config.
    render_result = dynamic_config_service.render(selected)
    payload = render_result.payload

    # Write output.
    envoy_dir = config_root / "envoy"
    envoy_dir.mkdir(parents=True, exist_ok=True)
    output_path = envoy_dir / "envoy.yaml"

    import yaml

    with open(output_path, "w") as f:
        yaml.dump(payload, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(
        f"[OK] Envoy config written to {output_path} "
        f"(routes={render_result.route_count}, clusters={render_result.cluster_count})"
    )


if __name__ == "__main__":
    main()
