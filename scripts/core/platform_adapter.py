"""Platform adapter contracts for deployment/rebuild orchestration.

This module defines target-agnostic contracts so orchestration flows can
dispatch platform lifecycle actions (k8s and compose) through a single
interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

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
    run_kubectl: RunKubectlFn | None = None
    docker_client: object | None = None
    compose_file: Path | None = None
    compose_env_file: Path | None = None
    compose_project_name: str = ""
    compose_profiles: tuple[str, ...] = ()
    selected_apps: tuple[str, ...] = ()
    internet_exposed: bool = False
    route_strategy: str = "subdomain"
    app_gateway_host: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    auth_provider: str = "none"
    auth_middleware: str = ""
    wait_timeout: str = "20m"
    node_ip: str = ""


def normalize_platform_target(target: str) -> str:
    normalized = str(target or "").strip().lower()
    if normalized in {"k8s", "kubernetes", "microk8s"}:
        return "k8s"
    if normalized in {"compose", "docker-compose", "docker_compose"}:
        return "compose"
    return normalized


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

    if resolved_target == "k8s":
        from core.kubernetes_rebuild_platform_adapter import (
            KubernetesRebuildPlatformAdapter,
            KubernetesRebuildPlatformConfig,
        )

        return KubernetesRebuildPlatformAdapter(
            cfg=KubernetesRebuildPlatformConfig(
                namespace=request.environment_id,
                target=resolved_target,
            ),
            namespace_service=_require_dependency(
                request, request.namespace_service, "namespace_service"
            ),
            manifest_apply_service=_require_dependency(
                request, request.manifest_apply_service, "manifest_apply_service"
            ),
            ingress_service=_require_dependency(
                request, request.ingress_service, "ingress_service"
            ),
            deployments_wait_service=_require_dependency(
                request,
                request.deployments_wait_service,
                "deployments_wait_service",
            ),
            smoke_test_service=_require_dependency(
                request, request.smoke_test_service, "smoke_test_service"
            ),
            info=request.info,
            run_kubectl=_require_dependency(request, request.run_kubectl, "run_kubectl"),
        )

    if resolved_target == "compose":
        from core.compose_rebuild_platform_adapter import (
            ComposeRebuildPlatformAdapter,
            ComposeRebuildPlatformConfig,
        )

        compose_file = request.compose_file
        if compose_file is None:
            raise ValueError(
                "Missing required dependency for platform target "
                f"'{request.target}': compose_file"
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
                app_gateway_host=request.app_gateway_host,
                app_path_prefix=request.app_path_prefix,
                media_server_direct_host=request.media_server_direct_host,
                auth_provider=request.auth_provider,
                auth_middleware=request.auth_middleware,
                wait_timeout=request.wait_timeout,
                node_ip=request.node_ip,
                target=resolved_target,
            ),
            info=request.info,
            docker=_require_dependency(request, request.docker_client, "docker_client"),
        )

    raise ValueError(
        f"Unsupported rebuild platform target '{request.target}'. "
        "Supported targets: k8s, compose."
    )
