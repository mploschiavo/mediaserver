"""Kubernetes implementation of rebuild platform adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.platform_adapter import InfoFn, PlatformEnvironmentRef, RunKubectlFn


class NamespaceService(Protocol):
    def delete_namespace_optional(self, delete_namespace: str) -> bool: ...


class ManifestApplyService(Protocol):
    def apply_manifests_for_profile(self) -> None: ...


class IngressService(Protocol):
    def patch_ingress_class(self) -> bool: ...


class DeploymentsWaitService(Protocol):
    def wait_for_deployments(self) -> None: ...


class SmokeTestService(Protocol):
    def run_smoke_test(self) -> str: ...


@dataclass(frozen=True)
class KubernetesRebuildPlatformConfig:
    namespace: str
    target: str = "k8s"


@dataclass
class KubernetesRebuildPlatformAdapter:
    cfg: KubernetesRebuildPlatformConfig
    namespace_service: NamespaceService
    manifest_apply_service: ManifestApplyService
    ingress_service: IngressService
    deployments_wait_service: DeploymentsWaitService
    smoke_test_service: SmokeTestService
    info: InfoFn
    run_kubectl: RunKubectlFn
    environment: PlatformEnvironmentRef = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment",
            PlatformEnvironmentRef(
                environment_id=self.cfg.namespace,
                target=self.cfg.target,
            ),
        )

    def delete_environment_optional(self, delete_environment: str) -> bool:
        return self.namespace_service.delete_namespace_optional(delete_environment)

    def apply_environment_definition(self) -> None:
        self.manifest_apply_service.apply_manifests_for_profile()

    def reconcile_edge_routing(self) -> bool:
        return self.ingress_service.patch_ingress_class()

    def wait_for_workloads(self) -> None:
        self.deployments_wait_service.wait_for_deployments()

    def run_smoke_test(self) -> str:
        return self.smoke_test_service.run_smoke_test()

    def print_workload_status(self) -> None:
        self.info("Final pod status:")
        self.run_kubectl(["-n", self.cfg.namespace, "get", "pods"])
