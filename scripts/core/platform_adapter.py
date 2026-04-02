"""Platform adapter contracts for deployment/rebuild orchestration.

This module defines target-agnostic contracts so orchestration flows can
dispatch platform lifecycle actions (k8s today, compose later) through a
single interface.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol

InfoFn = Callable[[str], None]
RunKubectlFn = Callable[..., subprocess.CompletedProcess[str]]


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

        return ComposeRebuildPlatformAdapter(
            cfg=ComposeRebuildPlatformConfig(
                environment_id=request.environment_id,
                target=resolved_target,
            ),
            info=request.info,
        )

    raise ValueError(
        f"Unsupported rebuild platform target '{request.target}'. "
        "Supported targets: k8s, compose."
    )
