"""Compose implementation placeholder for rebuild platform adapter."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.platform_adapter import InfoFn, PlatformEnvironmentRef


@dataclass(frozen=True)
class ComposeRebuildPlatformConfig:
    environment_id: str
    target: str = "compose"


@dataclass
class ComposeRebuildPlatformAdapter:
    cfg: ComposeRebuildPlatformConfig
    info: InfoFn
    environment: PlatformEnvironmentRef = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment",
            PlatformEnvironmentRef(
                environment_id=self.cfg.environment_id,
                target=self.cfg.target,
            ),
        )

    def _unsupported(self, action: str) -> RuntimeError:
        return RuntimeError(
            "Compose rebuild target is recognized but not wired for action "
            f"'{action}'. This repository currently supports Kubernetes runtime "
            "deployments."
        )

    def delete_environment_optional(self, delete_environment: str) -> bool:
        if delete_environment != "1":
            return False
        raise self._unsupported("delete_environment_optional")

    def apply_environment_definition(self) -> None:
        raise self._unsupported("apply_environment_definition")

    def reconcile_edge_routing(self) -> bool:
        self.info("Compose target: ingress-class patch skipped.")
        return False

    def wait_for_workloads(self) -> None:
        raise self._unsupported("wait_for_workloads")

    def run_smoke_test(self) -> str:
        self.info("Compose target: smoke test skipped.")
        return ""

    def print_workload_status(self) -> None:
        self.info("Compose target: workload status collection is not configured.")
