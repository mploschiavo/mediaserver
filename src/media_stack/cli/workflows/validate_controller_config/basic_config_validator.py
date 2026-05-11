"""BasicConfigValidator — top-level bootstrap-config shape checks.

ADR-0015 Phase 7b. Pre-Phase-7b ``basic_checks`` was a single
~165-LoC method on :class:`ValidateControllerConfigCommand`
covering six independent concerns:

* root + required-keys + config_version
* download_clients type check
* adapter_hooks type + disallowed keys
* technology_bindings + client-vs-binding consistency
* operation_handlers + event_handlers hook-spec format
* rebuild / microk8s_reconcile / config_overlays sub-trees

Phase 7b keeps the public entry point (``check(cfg)``) but routes
each concern through its own private method. The two largest
sub-trees (media_server_operation_plans, microk8s_reconcile) live
on their own Strategy classes in :mod:`event_plan_validators` and
are composed into this validator's check pipeline.
"""

from __future__ import annotations

from media_stack.cli.workflows.validate_controller_config.event_plan_validators import (
    MediaServerOperationPlanValidator,
    Microk8sReconcileHookValidator,
)
from media_stack.services.enums import RunnerEvent


_DISALLOWED_ADAPTER_HOOK_KEYS: tuple[str, ...] = (
    "technology_aliases",
    "adapter_classes",
    "download_client_adapter_classes",
    "media_server_adapter_classes",
    "before_common_steps",
    "app_service_classes",
    "service_technology_map",
)
_SUPPORTED_CONFIG_VERSION = 2


class BasicConfigValidator:
    """Validator: walk the bootstrap config + collect structural errors."""

    def check(self, cfg: object) -> list[str]:
        errors: list[str] = []
        if not isinstance(cfg, dict):
            return ["$: config root must be an object"]

        self._check_required_keys(cfg, errors)
        self._check_config_version(cfg, errors)
        self._check_download_clients_type(cfg, errors)

        adapter_hooks = self._resolve_adapter_hooks(cfg, errors)
        bindings = self._resolve_bindings(cfg, errors)

        self._check_disallowed_adapter_hook_keys(adapter_hooks, errors)
        self._check_technology_bindings(bindings, errors)
        self._check_clients_vs_bindings(cfg, bindings, errors)
        self._check_adapter_hook_spec_blocks(adapter_hooks, errors)
        self._check_media_server_block(cfg, errors)
        self._check_secondary_adapter_hook_subtrees(adapter_hooks, errors)
        self._check_config_overlays(cfg, errors)

        return errors

    # ----- individual checks -------------------------------------------

    def _check_required_keys(self, cfg: dict, errors: list[str]) -> None:
        for key in ("technology_bindings",):
            if key not in cfg:
                errors.append(f"$: missing required key '{key}'")

    def _check_config_version(self, cfg: dict, errors: list[str]) -> None:
        if "config_version" not in cfg:
            return
        config_version = cfg.get("config_version")
        if not isinstance(config_version, int):
            errors.append("$.config_version: must be an integer")
        elif config_version != _SUPPORTED_CONFIG_VERSION:
            errors.append(
                f"$.config_version: unsupported version (expected {_SUPPORTED_CONFIG_VERSION})"
            )

    def _check_download_clients_type(self, cfg: dict, errors: list[str]) -> None:
        clients = cfg.get("download_clients")
        if clients is not None and not isinstance(clients, dict):
            errors.append("$.download_clients: must be an object")

    def _resolve_adapter_hooks(self, cfg: dict, errors: list[str]) -> dict:
        adapter_hooks = cfg.get("adapter_hooks")
        if adapter_hooks is not None and not isinstance(adapter_hooks, dict):
            errors.append("$.adapter_hooks: must be an object")
        return adapter_hooks if isinstance(adapter_hooks, dict) else {}

    def _resolve_bindings(self, cfg: dict, errors: list[str]) -> dict:
        bindings = cfg.get("technology_bindings")
        if bindings is not None and not isinstance(bindings, dict):
            errors.append("$.technology_bindings: must be an object")
        return bindings if isinstance(bindings, dict) else {}

    def _check_disallowed_adapter_hook_keys(
        self, adapter_hooks: dict, errors: list[str],
    ) -> None:
        for disallowed_key in _DISALLOWED_ADAPTER_HOOK_KEYS:
            value = adapter_hooks.get(disallowed_key)
            if value not in (None, {}):
                errors.append(
                    f"$.adapter_hooks.{disallowed_key}: unsupported. "
                    "Move adapter/service registration into plugin manifests."
                )

    def _check_technology_bindings(self, bindings: dict, errors: list[str]) -> None:
        media_server_key = self._bound_key(bindings, "media_server")
        request_manager_key = self._bound_key(bindings, "request_manager")
        if not media_server_key:
            errors.append("$.technology_bindings.media_server: required non-empty string")
        if "request_manager" in bindings:
            if not isinstance(bindings.get("request_manager"), str):
                errors.append("$.technology_bindings.request_manager: must be a string")
            elif not request_manager_key:
                errors.append(
                    "$.technology_bindings.request_manager: required non-empty string when set"
                )

    def _check_clients_vs_bindings(
        self, cfg: dict, bindings: dict, errors: list[str],
    ) -> None:
        clients = cfg.get("download_clients")
        if not isinstance(clients, dict):
            return
        torrent_client_key = self._bound_key(bindings, "torrent_client")
        usenet_client_key = self._bound_key(bindings, "usenet_client")
        for name in (torrent_client_key, usenet_client_key):
            if not name:
                continue
            if name not in clients:
                errors.append(f"$.download_clients: missing active client section '{name}'")

    def _bound_key(self, bindings: dict, name: str) -> str:
        return str(bindings.get(name, "") or "").strip().lower()

    def _check_adapter_hook_spec_blocks(
        self, adapter_hooks: dict, errors: list[str],
    ) -> None:
        legacy_hook_map = adapter_hooks.get("operation_handlers")
        if legacy_hook_map is not None:
            if not isinstance(legacy_hook_map, dict):
                errors.append("$.adapter_hooks.operation_handlers: must be an object")
            else:
                for impl, spec in legacy_hook_map.items():
                    path = f"$.adapter_hooks.operation_handlers.{impl}"
                    self._check_hook_spec_format(path, spec, errors)

        event_hook_map = adapter_hooks.get("event_handlers")
        if event_hook_map is None:
            return
        if not isinstance(event_hook_map, dict):
            errors.append("$.adapter_hooks.event_handlers: must be an object")
            return
        for event_name, event_handlers in event_hook_map.items():
            event_path = f"$.adapter_hooks.event_handlers.{event_name}"
            try:
                RunnerEvent.from_value(str(event_name))
            except ValueError:
                errors.append(
                    f"{event_path}: unsupported event; expected one of "
                    f"{', '.join(RunnerEvent.choices())}"
                )
                continue
            if not isinstance(event_handlers, dict):
                errors.append(f"{event_path}: must be an object")
                continue
            for impl, spec in event_handlers.items():
                self._check_hook_spec_format(f"{event_path}.{impl}", spec, errors)

    def _check_hook_spec_format(
        self, path: str, spec: object, errors: list[str],
    ) -> None:
        if spec in (None, ""):
            return
        if ":" not in str(spec):
            errors.append(
                f"{path}: invalid hook spec '{spec}' (expected module.submodule:Symbol)"
            )

    def _check_media_server_block(self, cfg: dict, errors: list[str]) -> None:
        plan_validator = MediaServerOperationPlanValidator(errors)
        adapter_hooks = cfg.get("adapter_hooks") if isinstance(cfg.get("adapter_hooks"), dict) else {}
        plan_validator.validate(
            adapter_hooks.get("media_server_event_plans")
            or adapter_hooks.get("media_server_operation_plans"),
            "$.adapter_hooks.media_server_operation_plans",
        )

        media_server_cfg = cfg.get("media_server")
        if media_server_cfg is not None and not isinstance(media_server_cfg, dict):
            errors.append("$.media_server: must be an object")
        if isinstance(media_server_cfg, dict):
            plan_validator.validate(
                media_server_cfg.get("operation_plans"),
                "$.media_server.operation_plans",
            )

    def _check_secondary_adapter_hook_subtrees(
        self, adapter_hooks: dict, errors: list[str],
    ) -> None:
        rebuild_hooks = adapter_hooks.get("rebuild")
        if rebuild_hooks is not None and not isinstance(rebuild_hooks, dict):
            errors.append("$.adapter_hooks.rebuild: must be an object")

        microk8s_reconcile_hooks = adapter_hooks.get("microk8s_reconcile")
        if microk8s_reconcile_hooks is not None and not isinstance(
            microk8s_reconcile_hooks, dict,
        ):
            errors.append("$.adapter_hooks.microk8s_reconcile: must be an object")
        if isinstance(microk8s_reconcile_hooks, dict):
            Microk8sReconcileHookValidator(errors).validate(
                microk8s_reconcile_hooks,
                "$.adapter_hooks.microk8s_reconcile",
            )

    def _check_config_overlays(self, cfg: dict, errors: list[str]) -> None:
        overlays = cfg.get("config_overlays")
        if overlays is None:
            return
        if not isinstance(overlays, dict):
            errors.append("$.config_overlays: must be an object")
            return
        for key in ("enabled",):
            if key in overlays and not isinstance(overlays.get(key), bool):
                errors.append(f"$.config_overlays.{key}: must be a boolean")
        for key in ("env", "base_path", "overlay_dir"):
            if key in overlays and not isinstance(overlays.get(key), str):
                errors.append(f"$.config_overlays.{key}: must be a string")
        if "env_overlays" in overlays and not isinstance(
            overlays.get("env_overlays"), dict,
        ):
            errors.append("$.config_overlays.env_overlays: must be an object")


__all__ = ["BasicConfigValidator"]
