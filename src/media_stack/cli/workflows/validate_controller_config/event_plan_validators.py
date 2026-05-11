"""Strategy validators for the two event-plan-shaped sub-trees.

ADR-0015 Phase 7b. Pre-Phase-7b these were
:func:`@staticmethod` helpers
(``_validate_media_server_operation_plans``,
``_validate_microk8s_reconcile_hooks``) on
:class:`ValidateControllerConfigCommand`. They share a structural
shape — walk a dict, append errors to a caller-owned list — so
they collapse to two Strategy classes here.

Both classes accept the ``errors`` list as a constructor arg so
the caller can accumulate findings across multiple validators
without each validator owning its own state.
"""

from __future__ import annotations

from media_stack.services.enums import RunnerEvent


class MediaServerOperationPlanValidator:
    """Strategy: validate ``adapter_hooks.media_server_*operation_plans``."""

    def __init__(self, errors: list[str]) -> None:
        self._errors = errors

    def validate(self, plans: object, path_prefix: str) -> None:
        if plans is None:
            return
        if not isinstance(plans, dict):
            self._errors.append(f"{path_prefix}: must be an object")
            return
        for backend, phase_map in plans.items():
            backend_path = f"{path_prefix}.{backend}"
            if not isinstance(phase_map, dict):
                self._errors.append(f"{backend_path}: must be an object")
                continue
            for phase_name, phase_cfg in phase_map.items():
                self._validate_phase(backend_path, phase_name, phase_cfg)

    def _validate_phase(
        self, backend_path: str, phase_name: str, phase_cfg: object,
    ) -> None:
        phase_path = f"{backend_path}.{phase_name}"
        steps = phase_cfg.get("steps") if isinstance(phase_cfg, dict) else phase_cfg
        if steps is None:
            return
        if not isinstance(steps, list):
            self._errors.append(f"{phase_path}.steps: must be an array")
            return
        for idx, step in enumerate(steps):
            self._validate_step(f"{phase_path}.steps[{idx}]", step)

    def _validate_step(self, step_path: str, step: object) -> None:
        if not isinstance(step, dict):
            self._errors.append(f"{step_path}: must be an object")
            return
        event_name = str(step.get("event") or "").strip()
        handler = str(step.get("handler") or "").strip()
        operation = str(step.get("operation") or "").strip()
        if not handler and operation:
            handler = operation
        if not event_name and operation:
            event_name = "RUN"
        if not handler:
            self._errors.append(f"{step_path}.handler: required non-empty string")
            self._errors.append(f"{step_path}.operation: required non-empty string")
        if event_name:
            try:
                RunnerEvent.from_value(event_name)
            except ValueError:
                self._errors.append(
                    f"{step_path}.event: unsupported event '{event_name}' "
                    f"(expected one of {', '.join(RunnerEvent.choices())})"
                )


class Microk8sReconcileHookValidator:
    """Strategy: validate the ``adapter_hooks.microk8s_reconcile`` sub-tree."""

    def __init__(self, errors: list[str]) -> None:
        self._errors = errors

    def validate(self, hooks: dict[str, object], path_prefix: str) -> None:
        self._validate_phase_plan(hooks.get("phase_plan"), path_prefix)
        for key in ("optional_deployments", "optional_manifest_paths"):
            value = hooks.get(key)
            if value is not None and not isinstance(value, list):
                self._errors.append(f"{path_prefix}.{key}: must be an array")
        self._validate_conditional_manifests(
            hooks.get("conditional_manifests"), path_prefix,
        )

    def _validate_phase_plan(self, phase_plan: object, path_prefix: str) -> None:
        if not isinstance(phase_plan, list) or not phase_plan:
            self._errors.append(f"{path_prefix}.phase_plan: must be a non-empty array")
            return
        for idx, step in enumerate(phase_plan):
            step_path = f"{path_prefix}.phase_plan[{idx}]"
            if not isinstance(step, dict):
                self._errors.append(f"{step_path}: must be an object")
                continue
            handler = str(step.get("handler") or "").strip()
            event_name = str(step.get("event") or "").strip()
            if not handler:
                self._errors.append(f"{step_path}.handler: required non-empty string")
            if not event_name:
                self._errors.append(f"{step_path}.event: required non-empty string")
            else:
                try:
                    RunnerEvent.from_value(event_name)
                except ValueError:
                    self._errors.append(
                        f"{step_path}.event: unsupported event '{event_name}' "
                        f"(expected one of {', '.join(RunnerEvent.choices())})"
                    )

    def _validate_conditional_manifests(
        self, conditional_manifests: object, path_prefix: str,
    ) -> None:
        if conditional_manifests is None:
            return
        if not isinstance(conditional_manifests, list):
            self._errors.append(f"{path_prefix}.conditional_manifests: must be an array")
            return
        for idx, item in enumerate(conditional_manifests):
            item_path = f"{path_prefix}.conditional_manifests[{idx}]"
            if not isinstance(item, dict):
                self._errors.append(f"{item_path}: must be an object")
                continue
            if not str(item.get("deployment") or "").strip():
                self._errors.append(f"{item_path}.deployment: required non-empty string")
            if not str(item.get("manifest_path") or "").strip():
                self._errors.append(f"{item_path}.manifest_path: required non-empty string")


__all__ = [
    "MediaServerOperationPlanValidator",
    "Microk8sReconcileHookValidator",
]
