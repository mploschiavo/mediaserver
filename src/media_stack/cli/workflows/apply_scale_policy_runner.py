"""ApplyScalePolicyRunner — enforce K8s deployment scale guardrails.

ADR-0015 Phase 7i. Pre-Phase-7i ``ApplyScalePolicyCommand`` in
commands/ had 5 ``@staticmethod`` decorators
(:envvar:`STATIC_METHOD_RATCHET` violations) and called the
static helpers via module-level aliases instead of ``self.``.
Phase 7i moves the runner to this module with proper instance
methods (no ``@staticmethod``) — net -5 on the static-method
ratchet.
"""

from __future__ import annotations

import os
from pathlib import Path

from media_stack.core.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import MediaStackError
from media_stack.services.controller_component_resolver import (
    resolve_bootstrap_component_plan,
)


_REPLICAS_DESIRED = 1
_REPLICAS_SCALED_TO_ZERO = 0


class ApplyScalePolicyRunner:
    """Workflow: enforce scale_policy.managed_apps + scale_to_zero_apps."""

    def env_truthy(self, value: str | None) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def deployment_exists(
        self, kubectl: list[str], namespace: str, name: str,
    ) -> bool:
        proc = run_command(
            [*kubectl, "-n", namespace, "get", "deploy", name],
            check=False,
        )
        return proc.returncode == 0

    def current_replicas(
        self, kubectl: list[str], namespace: str, name: str,
    ) -> int:
        proc = run_command(
            [
                *kubectl, "-n", namespace, "get", "deploy", name,
                "-o", "jsonpath={.spec.replicas}",
            ],
            check=False,
        )
        text = str(proc.stdout or "").strip()
        try:
            return int(text)
        except (TypeError, ValueError):
            return 1

    def scale_deployment(
        self,
        kubectl: list[str],
        *,
        namespace: str,
        name: str,
        replicas: int,
        dry_run: bool,
    ) -> None:
        if not self.deployment_exists(kubectl, namespace, name):
            return
        if dry_run:
            print(f"[DRY] scale deploy/{name} -> {replicas}")
            return
        run_command(
            [
                *kubectl, "-n", namespace, "scale", "deploy", name,
                f"--replicas={int(replicas)}",
            ],
            check=True,
        )
        print(f"[OK] scale deploy/{name} -> {replicas}")

    def default_config_file(self) -> Path:
        env_path = str(os.environ.get("CONFIG_FILE", "")).strip()
        if env_path:
            return Path(env_path)
        # parents[5] = repo root (this file at
        # src/media_stack/cli/workflows/...).
        root_dir = Path(__file__).resolve().parents[5]
        return root_dir / "contracts" / "media-stack.config.json"

    def run(
        self,
        *,
        config_file: Path,
        namespace: str,
        dry_run: bool,
        scale_to_zero: bool,
    ) -> int:
        if not namespace:
            raise MediaStackError("NAMESPACE must be non-empty")

        plan = resolve_bootstrap_component_plan(config_file)
        managed_apps = tuple(plan.managed_apps)
        scale_to_zero_apps = tuple(
            app for app in plan.scale_to_zero_apps if app in managed_apps
        )

        kubectl = kube_cmd()

        if managed_apps:
            print(f"[INFO] Managed apps from config: {', '.join(managed_apps)}")
        for app in managed_apps:
            if not self.deployment_exists(kubectl, namespace, app):
                continue
            replicas = self.current_replicas(kubectl, namespace, app)
            if replicas <= 0:
                self.scale_deployment(
                    kubectl,
                    namespace=namespace, name=app,
                    replicas=_REPLICAS_DESIRED, dry_run=dry_run,
                )

        if scale_to_zero:
            if scale_to_zero_apps:
                print(
                    f"[INFO] Scale-to-zero apps from config: "
                    f"{', '.join(scale_to_zero_apps)}"
                )
            for app in scale_to_zero_apps:
                self.scale_deployment(
                    kubectl,
                    namespace=namespace, name=app,
                    replicas=_REPLICAS_SCALED_TO_ZERO, dry_run=dry_run,
                )

        print("[OK] Scale policy applied.")
        return 0


__all__ = ["ApplyScalePolicyRunner"]
