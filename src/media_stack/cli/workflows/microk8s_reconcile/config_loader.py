"""ReconcileConfigLoader — Repository for the microk8s-reconcile bootstrap config.

ADR-0015 Phase 7a. The argparse entry-point in
``cli/commands/microk8s_reconcile_main.py`` hands this class a path
to ``contracts/media-stack.config.json``; the loader reads the
JSON, merges any platform-specific adapter hooks, and parses the
phase-plan + conditional-manifest sub-trees into the frozen
dataclasses defined in :mod:`models`.

Pre-Phase-7a this lived as ``Microk8sReconcileCommand._load_reconcile_hooks``
+ ``_parse_phase_plan`` in commands/. Splitting onto its own
class isolates the JSON-parsing responsibility (the part most
likely to surface a "this hook isn't shaped right" error) from
the dispatch logic in :class:`Microk8sReconcileService`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from media_stack.cli.workflows.microk8s_reconcile.models import (
    ConditionalManifestRule,
    Microk8sReconcileConfig,
    ReconcilePhaseStep,
)
from media_stack.core.exceptions import ConfigError
from media_stack.services.controller_component_resolver import (
    _merge_platform_adapter_hooks,
)
from media_stack.services.enums import RunnerEvent


class ReconcileConfigLoader:
    """Repository: load + parse the microk8s-reconcile bootstrap config."""

    def load_reconcile_hooks(self, config_file: Path) -> dict[str, object]:
        if not config_file.exists():
            raise ConfigError(f"Bootstrap config not found: {config_file}")
        try:
            payload = json.loads(config_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Invalid JSON in bootstrap config {config_file}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ConfigError(f"Bootstrap config root must be an object: {config_file}")

        payload = _merge_platform_adapter_hooks(payload, config_file.parent)

        adapter_hooks = payload.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            raise ConfigError("adapter_hooks must be an object in bootstrap config")

        reconcile_hooks = adapter_hooks.get("microk8s_reconcile")
        if not isinstance(reconcile_hooks, dict):
            raise ConfigError("adapter_hooks.microk8s_reconcile must be an object")
        return reconcile_hooks

    def parse_phase_plan(self, raw_plan: object) -> tuple[ReconcilePhaseStep, ...]:
        if not isinstance(raw_plan, list) or not raw_plan:
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.phase_plan must be a non-empty array"
            )
        steps: list[ReconcilePhaseStep] = []
        for idx, item in enumerate(raw_plan):
            if not isinstance(item, dict):
                raise ConfigError(
                    f"adapter_hooks.microk8s_reconcile.phase_plan[{idx}] must be an object"
                )
            event_raw = str(item.get("event") or "").strip()
            handler = str(item.get("handler") or "").strip()
            phase_name = str(item.get("phase_name") or "").strip() or handler
            if not event_raw:
                raise ConfigError(
                    f"adapter_hooks.microk8s_reconcile.phase_plan[{idx}].event is required"
                )
            if not handler:
                raise ConfigError(
                    f"adapter_hooks.microk8s_reconcile.phase_plan[{idx}].handler is required"
                )
            try:
                event = RunnerEvent.from_value(event_raw)
            except ValueError as exc:
                raise ConfigError(
                    f"adapter_hooks.microk8s_reconcile.phase_plan[{idx}].event "
                    f"'{event_raw}' is not a valid RunnerEvent"
                ) from exc

            steps.append(
                ReconcilePhaseStep(
                    phase_name=phase_name,
                    event=event,
                    handler=handler,
                    enabled=bool(item.get("enabled", True)),
                    when=item.get("when", True),
                )
            )
        return tuple(steps)

    def build_config(
        self,
        *,
        root_dir: Path,
        include_optional: bool,
        env: dict[str, str] | None = None,
    ) -> Microk8sReconcileConfig:
        """Resolve env + bootstrap-config hooks into a frozen config.

        ``env`` is constructor-injected so tests can hand-build the
        ambient view without trampling :mod:`os.environ`. The default
        (``None``) samples ``os.environ`` at call time, matching the
        pre-Phase-7a behaviour.
        """
        active_env = env if env is not None else os.environ
        config_file = Path(
            str(active_env.get("CONFIG_FILE")
                or root_dir / "contracts" / "media-stack.config.json")
        ).resolve()
        hooks = self.load_reconcile_hooks(config_file)

        raw_optional_deployments = hooks.get("optional_deployments")
        if not isinstance(raw_optional_deployments, list):
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.optional_deployments must be an array"
            )
        optional_deployments = tuple(
            str(item or "").strip()
            for item in raw_optional_deployments
            if str(item or "").strip()
        )

        raw_optional_manifests = hooks.get("optional_manifest_paths")
        if not isinstance(raw_optional_manifests, list):
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.optional_manifest_paths must be an array"
            )
        optional_manifest_paths = tuple(
            (root_dir / str(item or "").strip()).resolve()
            for item in raw_optional_manifests
            if str(item or "").strip()
        )

        raw_conditional_manifests = hooks.get("conditional_manifests") or []
        if not isinstance(raw_conditional_manifests, list):
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.conditional_manifests must be an array"
            )
        conditional_manifest_rules: list[ConditionalManifestRule] = []
        for idx, item in enumerate(raw_conditional_manifests):
            if not isinstance(item, dict):
                raise ConfigError(
                    "adapter_hooks.microk8s_reconcile.conditional_manifests"
                    f"[{idx}] must be an object"
                )
            deployment = str(item.get("deployment") or "").strip()
            manifest = str(item.get("manifest_path") or "").strip()
            message = str(item.get("message") or "").strip()
            if not deployment or not manifest:
                raise ConfigError(
                    "adapter_hooks.microk8s_reconcile.conditional_manifests"
                    f"[{idx}] requires deployment and manifest_path"
                )
            conditional_manifest_rules.append(
                ConditionalManifestRule(
                    deployment=deployment,
                    manifest_path=(root_dir / manifest).resolve(),
                    message=message,
                )
            )

        phase_plan = self.parse_phase_plan(hooks.get("phase_plan"))

        return Microk8sReconcileConfig(
            namespace=str(active_env.get("NAMESPACE", "media-stack")).strip() or "media-stack",
            wait_timeout=str(active_env.get("WAIT_TIMEOUT", "20m")).strip() or "20m",
            include_optional=include_optional,
            root_dir=root_dir,
            optional_deployments=optional_deployments,
            optional_manifest_paths=optional_manifest_paths,
            conditional_manifest_rules=tuple(conditional_manifest_rules),
            phase_plan=phase_plan,
        )


__all__ = ["ReconcileConfigLoader"]
