"""ManifestApplier — Strategy for the three apply-manifest phase handlers.

ADR-0015 Phase 7a. Pre-Phase-7a the three apply handlers
(``_handle_apply_base_kustomize``, ``_handle_apply_optional_manifests``,
``_handle_apply_conditional_manifests``) sat as methods on
:class:`Microk8sReconcileRunner` alongside the rollout handlers
+ dispatch loop. Splitting onto this class isolates the
"talk to kubectl apply" responsibility from the rollout-monitoring
responsibility.

Strategy pattern: the service hands this class a kubectl-runner
callable and a cfg view; each handler method is invoked by the
dispatch loop on the corresponding phase step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from media_stack.core.exceptions import ConfigError

if TYPE_CHECKING:
    from media_stack.cli.workflows.microk8s_reconcile.models import (
        Microk8sReconcileConfig,
    )
    from media_stack.core.subprocess_utils import CommandResult


class ManifestApplier:
    """Strategy: three apply-manifest phase handlers."""

    def __init__(
        self,
        cfg: "Microk8sReconcileConfig",
        kubectl_runner: Callable[..., "CommandResult"],
    ) -> None:
        self._cfg = cfg
        self._kubectl = kubectl_runner

    def apply_base_kustomize(self) -> None:
        k8s_dir = self._cfg.root_dir / "deploy" / "k8s"
        if not k8s_dir.is_dir():
            raise ConfigError(f"deploy/k8s directory not found: {k8s_dir}")
        print(f"[INFO] Applying core manifests from {k8s_dir}")
        self._kubectl(["apply", "-k", str(k8s_dir)])

    def apply_optional_manifests(self) -> None:
        for manifest_path in self._cfg.optional_manifest_paths:
            print(f"[INFO] Applying optional manifests from {manifest_path}")
            self._kubectl(["apply", "-f", str(manifest_path)])

    def apply_conditional_manifests(self) -> None:
        for rule in self._cfg.conditional_manifest_rules:
            deployment_probe = self._kubectl(
                ["-n", self._cfg.namespace, "get", "deploy", rule.deployment],
                check=False,
            )
            if deployment_probe.returncode != 0:
                continue
            if not rule.manifest_path.is_file():
                raise ConfigError(
                    f"Conditional manifest configured but file not found: {rule.manifest_path}"
                )
            if rule.message:
                print(f"[INFO] {rule.message}")
            self._kubectl(["apply", "-f", str(rule.manifest_path)])


__all__ = ["ManifestApplier"]
