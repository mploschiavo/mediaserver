"""Platform adapter contracts for deployment/rebuild orchestration.

This module defines target-agnostic contracts so orchestration flows can
dispatch platform lifecycle actions (k8s and compose) through a single
interface.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from core.platform_plugin_registry import (
    available_platform_targets,
    resolve_platform_plugin,
)
from core.platform_plugin_registry import (
    normalize_platform_target as _normalize_platform_target_from_registry,
)

InfoFn = Callable[[str], None]
RunKubectlFn = Callable[..., Any]


@dataclass(frozen=True)
class PlatformEnvironmentRef:
    """Logical deployment environment identity across platforms."""

    environment_id: str
    target: str


class RebuildPlatformAdapter(Protocol):
    """Target-specific adapter used by rebuild orchestration."""

    environment: PlatformEnvironmentRef

    def delete_environment_optional(self, delete_environment: str) -> bool:
        """Delete environment when policy/flags request destructive rebuild."""

    def apply_environment_definition(self) -> None:
        """Apply target runtime definition (k8s manifests, compose files, etc.)."""

    def reconcile_edge_routing(self) -> bool:
        """Reconcile ingress/edge routing contract for user-facing access."""

    def wait_for_workloads(self) -> None:
        """Block until target workloads become healthy."""

    def run_smoke_test(self) -> str:
        """Run target smoke checks and return resolved endpoint/IP when relevant."""

    def print_workload_status(self) -> None:
        """Emit final workload status snapshot for diagnostics."""

    def backup_secret_values(self, preserve_secret_on_rebuild: str) -> dict[str, str]:
        """Backup preserved credentials when target supports secret lifecycle."""

    def restore_secret_values(self, values: dict[str, str]) -> None:
        """Restore preserved credentials when target supports secret lifecycle."""


@dataclass(frozen=True)
class RebuildPlatformAdapterBuildRequest:
    target: str
    environment_id: str
    info: InfoFn
    namespace_service: object | None = None
    manifest_apply_service: object | None = None
    ingress_service: object | None = None
    deployments_wait_service: object | None = None
    smoke_test_service: object | None = None
    secret_preservation_service: object | None = None
    run_kubectl: RunKubectlFn | None = None
    runtime_artifacts_dir: Path | None = None
    docker_client: object | None = None
    compose_file: Path | None = None
    compose_env_file: Path | None = None
    compose_project_name: str = ""
    compose_profiles: tuple[str, ...] = ()
    selected_apps: tuple[str, ...] = ()
    internet_exposed: bool = False
    route_strategy: str = "subdomain"
    allowed_route_strategies: tuple[str, ...] = ()
    app_gateway_host: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    auth_provider: str = ""
    auth_middleware: str = ""
    edge_router_provider: str = ""
    edge_router_service_names: tuple[str, ...] = ()
    edge_compose_provider_specs: dict[str, dict[str, str]] | None = None
    auth_provider_middleware_defaults: dict[str, str] | None = None
    media_server_service_names: tuple[str, ...] = ()
    wait_timeout: str = "20m"
    node_ip: str = ""


def normalize_platform_target(target: str) -> str:
    return _normalize_platform_target_from_registry(target)


def _require_dependency(
    request: RebuildPlatformAdapterBuildRequest, value: object | None, name: str
) -> object:
    if value is None:
        raise ValueError(
            "Missing required dependency for platform target " f"'{request.target}': {name}"
        )
    return value


def build_rebuild_platform_adapter(
    request: RebuildPlatformAdapterBuildRequest,
) -> RebuildPlatformAdapter:
    resolved_target = normalize_platform_target(request.target)
    plugin = resolve_platform_plugin(resolved_target)
    if plugin is None:
        available = ", ".join(available_platform_targets())
        raise ValueError(
            f"Unsupported rebuild platform target '{request.target}'. "
            f"Supported targets: {available}."
        )
    normalized_request = replace(request, target=resolved_target)
    return plugin.build_adapter(normalized_request, _require_dependency)
