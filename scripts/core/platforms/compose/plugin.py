"""Compose platform plugin bindings."""

from __future__ import annotations

from core.platform_plugin_contract import PlatformPlugin
from core.platforms.compose.bootstrap_service import ComposeBootstrapConfig, ComposeBootstrapService
from core.platforms.compose.docker_client import DockerClient
from core.platforms.compose.rebuild_platform_adapter import (
    ComposeRebuildPlatformAdapter,
    ComposeRebuildPlatformConfig,
)


def _docker_client(runner: object) -> DockerClient:
    return runner.get_or_create_platform_client("docker", DockerClient.from_environment)


def _build_adapter(request: object, require_dependency) -> object:
    compose_file = request.compose_file
    if compose_file is None:
        raise ValueError(
            "Missing required dependency for platform target " f"'{request.target}': compose_file"
        )
    return ComposeRebuildPlatformAdapter(
        cfg=ComposeRebuildPlatformConfig(
            environment_id=request.environment_id,
            compose_file=compose_file,
            compose_env_file=request.compose_env_file,
            compose_project_name=request.compose_project_name,
            compose_profiles=tuple(request.compose_profiles or ()),
            selected_apps=tuple(request.selected_apps or ()),
            internet_exposed=bool(request.internet_exposed),
            route_strategy=request.route_strategy,
            allowed_route_strategies=tuple(request.allowed_route_strategies or ()),
            app_gateway_host=request.app_gateway_host,
            app_path_prefix=request.app_path_prefix,
            media_server_direct_host=request.media_server_direct_host,
            auth_provider=request.auth_provider,
            auth_middleware=request.auth_middleware,
            edge_router_provider=request.edge_router_provider,
            edge_router_service_names=tuple(request.edge_router_service_names or ()),
            edge_compose_provider_specs=dict(request.edge_compose_provider_specs or {}),
            auth_provider_middleware_defaults=dict(request.auth_provider_middleware_defaults or {}),
            media_server_service_names=tuple(request.media_server_service_names or ()),
            wait_timeout=request.wait_timeout,
            node_ip=request.node_ip,
            disk_allocation_gb=int(request.disk_allocation_gb or 500),
            runtime_artifacts_dir=request.runtime_artifacts_dir,
            target=request.target,
        ),
        info=request.info,
        docker=require_dependency(request, request.docker_client, "docker_client"),
    )


def _build_runner_request(runner: object, info_fn) -> dict[str, object]:
    target = runner._resolved_platform_target()
    return {
        "target": target,
        "environment_id": runner.cfg.namespace,
        "info": info_fn,
        "docker_client": _docker_client(runner),
        "compose_file": runner.cfg.compose_file,
        "compose_env_file": runner.cfg.compose_env_file,
        "compose_project_name": runner.cfg.compose_project_name,
        "compose_profiles": runner._compose_profiles(),
        "selected_apps": runner._selected_apps(),
        "internet_exposed": runner._is_truthy(runner.cfg.internet_exposed),
        "route_strategy": runner.cfg.route_strategy,
        "allowed_route_strategies": runner._valid_route_strategies(),
        "app_gateway_host": runner.cfg.app_gateway_host,
        "app_path_prefix": runner.cfg.app_path_prefix,
        "media_server_direct_host": runner.cfg.media_server_direct_host,
        "auth_provider": runner.cfg.auth_provider,
        "auth_middleware": runner.cfg.auth_middleware,
        "edge_router_provider": runner._edge_router_provider(),
        "edge_router_service_names": runner._edge_router_service_names(),
        "edge_compose_provider_specs": runner._edge_compose_provider_specs(),
        "auth_provider_middleware_defaults": runner._auth_provider_middleware_defaults(),
        "media_server_service_names": runner._media_server_service_names(),
        "wait_timeout": runner.cfg.wait_timeout,
        "node_ip": runner.cfg.node_ip,
        "disk_allocation_gb": int(runner.cfg.disk_allocation_gb),
        "runtime_artifacts_dir": runner.runtime_artifacts_target_dir("compose"),
    }


def _configure_runner(runner: object) -> None:
    runner.kube = None


def _run_bootstrap(runner: object) -> None:
    service = ComposeBootstrapService(
        cfg=ComposeBootstrapConfig(
            namespace=runner.cfg.namespace,
            compose_file=runner.cfg.compose_file,
            compose_env_file=runner.cfg.compose_env_file,
            compose_project_name=runner.cfg.compose_project_name,
            bootstrap_runner_image=runner.cfg.bootstrap_runner_image,
            bootstrap_config_file=runner.cfg.config_file,
            wait_timeout=runner.cfg.wait_timeout,
            purpose=runner.cfg.purpose,
            preconfigure_api_keys=runner._is_truthy(runner.cfg.preconfigure_api_keys),
            apply_initial_preferences=runner._is_truthy(runner.cfg.apply_initial_preferences),
            auto_download_content=runner._is_truthy(runner.cfg.auto_download_content),
            runtime_config_policy_handler=runner._runtime_config_policy_handler_spec(),
            runtime_config_policy_params=runner._runtime_config_policy_params(),
            passthrough_env_vars=runner._compose_passthrough_env_vars(),
            preflight_handler_specs=runner._compose_preflight_handlers(),
        ),
        info=runner.info_fn,
        docker=_docker_client(runner),
    )
    service.run()


PLUGIN = PlatformPlugin(
    key="compose",
    aliases=("compose", "docker-compose", "docker_compose"),
    build_adapter=_build_adapter,
    build_runner_request=_build_runner_request,
    configure_runner=_configure_runner,
    run_bootstrap=_run_bootstrap,
    bootstrap_phase_name="Run compose bootstrap pipeline",
    supports_scale_policy_guardrails=False,
    requires_runtime_config_policy_handler=True,
    logs_bootstrap_runner_image=True,
)
