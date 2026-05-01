from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

try:  # pragma: no cover - import path depends on entrypoint context
    from media_stack.core.controller_profile import (
        ControllerProfileConfig,
        load_bootstrap_profile_catalog,
        maybe_load_bootstrap_profile,
        normalize_selected_apps_csv,
    )
    from media_stack.core.platform_cli_defaults_registry import resolve_platform_cli_defaults
    from media_stack.core.platform_plugin_registry import normalize_platform_target
    from media_stack.core.defaults import default_controller_image
    from media_stack.core.platforms.compose.deploy_cli_options import resolve_compose_file_paths
except ModuleNotFoundError:  # pragma: no cover
    from media_stack.core.controller_profile import (
        ControllerProfileConfig,
        load_bootstrap_profile_catalog,
        maybe_load_bootstrap_profile,
        normalize_selected_apps_csv,
    )
    from media_stack.core.platform_cli_defaults_registry import resolve_platform_cli_defaults
    from media_stack.core.platform_plugin_registry import normalize_platform_target
    from media_stack.core.defaults import default_controller_image
    from media_stack.core.platforms.compose.deploy_cli_options import resolve_compose_file_paths

DEFAULT_PREPARE_HOST_ROOT = Path("/", "srv", "media-stack").as_posix()


@dataclass
class DeployStackConfig:
    root_dir: Path
    platform_target: str = "k8s"
    namespace: str = "media-stack"
    secret_name: str = "media-stack-secrets"
    wait_timeout: str = "20m"
    delete_namespace: str = "0"
    delete_namespace_confirm: str = ""
    include_optional: str = ""
    enable_components: str = ""
    run_bootstrap: str = ""
    preconfigure_api_keys: str = "1"
    apply_initial_preferences: str = "1"
    auto_download_content: str = "0"
    run_smoke_test: str = "1"
    skip_prepare_host: str = "0"
    prepare_host_root: str = DEFAULT_PREPARE_HOST_ROOT
    storage_mode: str = "dynamic-pvc"
    pvc_storage_class: str = ""
    ingress_domain: str = "local"
    config_file: Path = Path("contracts/media-stack.config.json")
    ingress_class: str = "auto"
    profile: str = "full"
    alert_webhook_url: str = ""
    generate_secrets_on_rebuild: str = "0"
    preserve_secret_on_rebuild: str = "1"
    node_ip: str = ""
    compose_file: Path = Path("deploy/compose/docker-compose.yml")
    compose_env_file: Path = Path("deploy/compose/.env")
    compose_project_name: str = ""
    compose_profiles: str = ""
    bootstrap_runner_image: str = ""

    def __post_init__(self) -> None:
        if not self.bootstrap_runner_image:
            self.bootstrap_runner_image = default_controller_image()
    selected_apps: str = ""
    purpose: str = "dev"
    disk_allocation_gb: int = 500
    network_cidr: str = "192.168.1.0/24"
    internet_exposed: str = "0"
    route_strategy: str = "subdomain"
    app_gateway_host: str = ""
    app_gateway_port: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    auth_provider: str = ""
    auth_middleware: str = ""
    edge_router_provider: str = ""
    chaos_enabled: str = "0"
    chaos_duration_minutes: int = 5
    chaos_interval_seconds: int = 60
    chaos_actions: str = "restart_container,pause_container,network_disconnect"
    bootstrap_profile_file: Path | None = None


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    token = str(value).strip()
    return token if token else None


def _pick(*values: str | None, default: str = "") -> str:
    for value in values:
        if value is not None and str(value) != "":
            return str(value)
    return default


def _resolve_profile_path(
    *,
    parsed_profile_path: str | None,
    root_dir: Path,
) -> Path | None:
    if parsed_profile_path and parsed_profile_path.strip():
        return Path(parsed_profile_path).expanduser()
    env_profile = _env_value("BOOTSTRAP_PROFILE_FILE")
    if env_profile:
        return Path(env_profile).expanduser()
    default_path = root_dir / "contracts" / "media-stack.profile.yaml"
    if default_path.exists():
        return default_path
    return None


def _normalize_path_prefix(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return "/app"
    if not token.startswith("/"):
        token = f"/{token}"
    token = token.rstrip("/")
    return token or "/app"


def _resolve_profile_defaults(
    profile: ControllerProfileConfig | None,
) -> dict[str, str]:
    if profile is None:
        return {}
    ingress_domain = profile.exposure.ingress_domain
    return {
        "platform_target": profile.deployment_target,
        "namespace": profile.stack_name,
        "compose_project_name": profile.stack_name,
        "run_bootstrap": "1" if profile.preconfigure_apps else "0",
        "preconfigure_api_keys": "1" if profile.preconfigure_api_keys else "0",
        "apply_initial_preferences": "1" if profile.apply_initial_preferences else "0",
        "auto_download_content": "1" if profile.auto_download_content else "0",
        "selected_apps": profile.selected_apps_csv,
        "purpose": profile.purpose,
        "profile": str(profile.install_profile or "").strip().lower(),
        "disk_allocation_gb": str(profile.disk_allocation_gb),
        "network_cidr": profile.network_cidr,
        "internet_exposed": "1" if profile.exposure.internet_exposed else "0",
        "route_strategy": profile.exposure.route_strategy,
        "app_gateway_host": profile.exposure.gateway_host,
        "app_gateway_port": profile.exposure.gateway_port,
        "app_path_prefix": profile.exposure.normalized_app_path_prefix,
        "media_server_direct_host": profile.exposure.media_server_direct_host,
        "auth_provider": profile.exposure.auth_provider,
        "auth_middleware": profile.exposure.auth_middleware,
        "edge_router_provider": profile.exposure.edge_router_provider,
        "chaos_enabled": "1" if profile.chaos.enabled else "0",
        "chaos_duration_minutes": str(profile.chaos.duration_minutes),
        "chaos_interval_seconds": str(profile.chaos.interval_seconds),
        "chaos_actions": ",".join(profile.chaos.actions),
        "ingress_domain": ingress_domain,
    }


def parse_deploy_stack_config(argv: list[str], *, root_dir: Path) -> DeployStackConfig:
    profile_catalog = load_bootstrap_profile_catalog()
    route_values = tuple(dict.fromkeys(profile_catalog.route_strategy_aliases.values()))
    default_route_strategy = route_values[0] if route_values else "subdomain"
    default_auth_provider = str(profile_catalog.auth_disabled_provider or "").strip().lower()
    if not default_auth_provider:
        default_auth_provider = (
            str(profile_catalog.auth_providers[0] or "").strip().lower()
            if profile_catalog.auth_providers
            else ""
        )

    parser = argparse.ArgumentParser(
        prog="bin/deploy-stack.sh",
        description=(
            "Deploy and bootstrap the media stack. "
            "All deployment settings are sourced from the bootstrap profile YAML. "
            "See deploy/examples/bootstrap-profiles/ for reference profiles."
        ),
    )
    parser.add_argument(
        "node_ip",
        nargs="?",
        default=None,
        metavar="NODE_IP",
        help="Cluster/host node IP (k8s only). Falls back to NODE_IP env var.",
    )
    parser.add_argument(
        "--bootstrap-profile-file",
        default=None,
        metavar="FILE",
        help=(
            "Path to a bootstrap profile YAML (see deploy/examples/bootstrap-profiles/). "
            "Falls back to BOOTSTRAP_PROFILE_FILE env var or "
            "contracts/media-stack.profile.yaml if present."
        ),
    )
    parsed = parser.parse_args(argv)

    profile_path = _resolve_profile_path(
        parsed_profile_path=parsed.bootstrap_profile_file,
        root_dir=root_dir,
    )
    profile = maybe_load_bootstrap_profile(profile_path)
    profile_defaults = _resolve_profile_defaults(profile)

    requested_platform_target = _pick(
        _env_value("PLATFORM_TARGET"),
        profile_defaults.get("platform_target"),
        default="k8s",
    )
    normalized_platform_target = normalize_platform_target(requested_platform_target)
    platform_target = normalized_platform_target or str(requested_platform_target or "").strip()
    platform_defaults = resolve_platform_cli_defaults(target=platform_target, root_dir=root_dir)
    default_compose_file = platform_defaults.compose_file or (
        root_dir / DeployStackConfig.compose_file
    )
    default_compose_env_file = platform_defaults.compose_env_file or (
        root_dir / DeployStackConfig.compose_env_file
    )

    compose_file, compose_env_file = resolve_compose_file_paths(
        parsed_compose_file=None,
        parsed_compose_env_file=None,
        env_compose_file=_env_value("COMPOSE_FILE"),
        env_compose_env_file=_env_value("COMPOSE_ENV_FILE"),
        default_compose_file=default_compose_file,
        default_compose_env_file=default_compose_env_file,
    )
    selected_apps = normalize_selected_apps_csv(
        _pick(
            _env_value("SELECTED_APPS"),
            profile_defaults.get("selected_apps"),
            default="",
        )
    )

    return DeployStackConfig(
        root_dir=root_dir,
        platform_target=platform_target,
        namespace=_pick(
            _env_value("NAMESPACE"),
            profile_defaults.get("namespace"),
            default="media-stack",
        ),
        secret_name=_pick(_env_value("SECRET_NAME"), default="media-stack-secrets"),
        wait_timeout=_pick(_env_value("WAIT_TIMEOUT"), default="20m"),
        delete_namespace=_pick(_env_value("DELETE_NAMESPACE"), default="0"),
        delete_namespace_confirm=_pick(_env_value("DELETE_NAMESPACE_CONFIRM"), default=""),
        include_optional=_pick(_env_value("INCLUDE_OPTIONAL"), default=""),
        enable_components=_pick(_env_value("ENABLE_COMPONENTS"), default=""),
        run_bootstrap=_pick(
            _env_value("RUN_BOOTSTRAP"),
            profile_defaults.get("run_bootstrap"),
            default="",
        ),
        preconfigure_api_keys=_pick(
            _env_value("PRECONFIGURE_API_KEYS"),
            profile_defaults.get("preconfigure_api_keys"),
            default="1",
        ),
        apply_initial_preferences=_pick(
            _env_value("APPLY_INITIAL_PREFERENCES"),
            _env_value("FULLY_PRECONFIGURED"),
            profile_defaults.get("apply_initial_preferences"),
            default="1",
        ),
        auto_download_content=_pick(
            _env_value("AUTO_DOWNLOAD_CONTENT"),
            profile_defaults.get("auto_download_content"),
            default="0",
        ),
        run_smoke_test=_pick(_env_value("RUN_SMOKE_TEST"), default="1"),
        skip_prepare_host=_pick(_env_value("SKIP_PREPARE_HOST"), default="0"),
        prepare_host_root=_pick(_env_value("PREPARE_HOST_ROOT"), default=DEFAULT_PREPARE_HOST_ROOT),
        storage_mode=_pick(_env_value("STORAGE_MODE"), default="dynamic-pvc"),
        pvc_storage_class=_pick(_env_value("PVC_STORAGE_CLASS"), default=""),
        ingress_domain=_pick(
            _env_value("INGRESS_DOMAIN"),
            profile_defaults.get("ingress_domain"),
            default="local",
        ),
        config_file=Path(
            _pick(
                _env_value("CONFIG_FILE"),
                default=str(root_dir / "contracts" / "media-stack.config.json"),
            )
        ),
        ingress_class=_pick(_env_value("INGRESS_CLASS"), default="auto"),
        profile=_pick(
            _env_value("PROFILE"),
            profile_defaults.get("profile"),
            default="full",
        ),
        alert_webhook_url=_pick(_env_value("ALERT_WEBHOOK_URL"), default=""),
        generate_secrets_on_rebuild=_pick(_env_value("GENERATE_SECRETS_ON_REBUILD"), default="0"),
        preserve_secret_on_rebuild=_pick(_env_value("PRESERVE_SECRET_ON_REBUILD"), default="1"),
        node_ip=_pick(parsed.node_ip, _env_value("NODE_IP"), default=""),
        compose_file=compose_file,
        compose_env_file=compose_env_file,
        compose_project_name=_pick(
            _env_value("COMPOSE_PROJECT_NAME"),
            profile_defaults.get("compose_project_name"),
            default="",
        ),
        compose_profiles=_pick(_env_value("COMPOSE_PROFILES"), default=""),
        bootstrap_runner_image=_pick(
            _env_value("BOOTSTRAP_RUNNER_IMAGE"),
            default=default_controller_image(),
        ),
        selected_apps=selected_apps,
        purpose=_pick(
            _env_value("BOOTSTRAP_PURPOSE"), profile_defaults.get("purpose"), default="dev"
        ),
        disk_allocation_gb=int(
            _pick(
                _env_value("STACK_DISK_ALLOCATION_GB"),
                profile_defaults.get("disk_allocation_gb"),
                default="500",
            )
        ),
        network_cidr=_pick(
            _env_value("STACK_NETWORK_CIDR"),
            profile_defaults.get("network_cidr"),
            default="192.168.1.0/24",
        ),
        internet_exposed=_pick(
            _env_value("INTERNET_EXPOSED"),
            profile_defaults.get("internet_exposed"),
            default="0",
        ),
        route_strategy=_pick(
            _env_value("ROUTE_STRATEGY"),
            profile_defaults.get("route_strategy"),
            default=default_route_strategy,
        )
        .strip()
        .lower(),
        app_gateway_host=_pick(
            _env_value("APP_GATEWAY_HOST"),
            profile_defaults.get("app_gateway_host"),
            default="",
        ),
        app_gateway_port=_pick(
            _env_value("APP_GATEWAY_PORT"),
            _env_value("EDGE_HTTP_PORT"),
            _env_value("TRAEFIK_HTTP_PORT"),
            profile_defaults.get("app_gateway_port"),
            default="",
        ),
        app_path_prefix=_normalize_path_prefix(
            _pick(
                _env_value("APP_PATH_PREFIX"),
                profile_defaults.get("app_path_prefix"),
                default="/app",
            )
        ),
        media_server_direct_host=_pick(
            _env_value("MEDIA_SERVER_DIRECT_HOST"),
            profile_defaults.get("media_server_direct_host"),
            default="",
        ),
        auth_provider=_pick(
            _env_value("AUTH_PROVIDER"),
            profile_defaults.get("auth_provider"),
            default=default_auth_provider,
        )
        .strip()
        .lower(),
        auth_middleware=_pick(
            _env_value("AUTH_MIDDLEWARE"),
            profile_defaults.get("auth_middleware"),
            default="",
        ),
        edge_router_provider=_pick(
            _env_value("EDGE_ROUTER_PROVIDER"),
            profile_defaults.get("edge_router_provider"),
            default="",
        )
        .strip()
        .lower(),
        chaos_enabled=_pick(
            _env_value("CHAOS_ENABLED"),
            profile_defaults.get("chaos_enabled"),
            default="0",
        ),
        chaos_duration_minutes=int(
            _pick(
                _env_value("CHAOS_DURATION_MINUTES"),
                profile_defaults.get("chaos_duration_minutes"),
                default="5",
            )
        ),
        chaos_interval_seconds=int(
            _pick(
                _env_value("CHAOS_INTERVAL_SECONDS"),
                profile_defaults.get("chaos_interval_seconds"),
                default="60",
            )
        ),
        chaos_actions=_pick(
            _env_value("CHAOS_ACTIONS"),
            profile_defaults.get("chaos_actions"),
            default="restart_container,pause_container,network_disconnect",
        ),
        bootstrap_profile_file=profile_path,
    )
