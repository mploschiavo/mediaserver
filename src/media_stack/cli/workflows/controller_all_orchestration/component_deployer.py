"""ComponentDeployer — Strategy for applying + scaling component manifests.

ADR-0015 Phase 7d. Pre-Phase-7d three methods on
:class:`ControllerAllRunner` (``_manifest_overrides``,
``_apply_manifest_file``, ``_enable_component_deployment``)
handled the deployment-enablement responsibility. Splitting onto
this class isolates the "talk to kubectl apply + rollout" part
from the orchestration loop.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.services.controller_component_resolver import (
    resolve_component_deployment_name,
    resolve_component_manifest_path,
)

if TYPE_CHECKING:
    from media_stack.cli.workflows.controller_all_orchestration.component_plan_resolver import (
        ComponentPlanResolver,
    )
    from media_stack.cli.workflows.controller_all_orchestration.models import (
        ControllerAllConfig,
    )
    from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


_ROLLOUT_TIMEOUT = "10m"


class ComponentDeployer:
    """Strategy: apply + scale + wait-rollout for a single component."""

    def __init__(
        self,
        cfg: "ControllerAllConfig",
        kube: "KubernetesClient",
        plan_resolver: "ComponentPlanResolver",
    ) -> None:
        self._cfg = cfg
        self._kube = kube
        self._plan_resolver = plan_resolver

    def manifest_overrides(self, text: str) -> str:
        out = re.sub(
            r"namespace:\s*media-stack\b",
            f"namespace: {self._cfg.namespace}",
            text,
        )
        out = re.sub(
            r"name:\s*media-stack\s*$",
            f"name: {self._cfg.namespace}",
            out,
            flags=re.MULTILINE,
        )
        return out.replace("/srv/media-stack", self._cfg.prepare_host_root)

    def apply_manifest_file(self, manifest_path: Path, *, component: str) -> None:
        if not manifest_path.is_file():
            raise ConfigError(
                f"Component manifest not found for '{component}': {manifest_path}"
            )
        patched_text = self.manifest_overrides(manifest_path.read_text(encoding="utf-8"))

        prefix_component = re.sub(r"[^a-z0-9-]+", "-", str(component or "").lower()).strip("-")
        prefix_component = prefix_component or "component"
        with TemporaryDirectory(prefix=f"media-stack-{prefix_component}-") as tmp:
            patched = Path(tmp) / manifest_path.name
            patched.write_text(patched_text, encoding="utf-8")
            result = self._kube.run(["apply", "-f", str(patched)], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                raise KubernetesError(result.stderr or result.stdout)

    def enable_component_deployment(self, component: str) -> None:
        plan = self._plan_resolver.component_plan()
        component_manifest = resolve_component_manifest_path(
            plan.config,
            component=component,
            aliases=plan.aliases,
        )
        manifest_path = (self._cfg.root_dir / component_manifest).resolve()
        if not manifest_path.is_file():
            raise ConfigError(
                f"Component manifest not found for '{component}': {manifest_path}"
            )

        deployment_name = resolve_component_deployment_name(
            plan.config,
            component=component,
            aliases=plan.aliases,
        )
        self.apply_manifest_file(manifest_path, component=component)
        self._kube.run(
            [
                "-n",
                self._cfg.namespace,
                "scale",
                f"deploy/{deployment_name}",
                "--replicas=1",
            ]
        )
        self._kube.run(
            [
                "-n",
                self._cfg.namespace,
                "rollout",
                "status",
                f"deploy/{deployment_name}",
                f"--timeout={_ROLLOUT_TIMEOUT}",
            ]
        )


__all__ = ["ComponentDeployer"]
