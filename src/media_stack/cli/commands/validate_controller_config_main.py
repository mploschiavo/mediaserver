#!/usr/bin/env python3
"""Validate bootstrap config against schema (with clear errors)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path



class ValidateControllerConfigCommand:
    def format_path(self, path_parts):
        if not path_parts:
            return "$"
        out = "$"
        for part in path_parts:
            if isinstance(part, int):
                out += f"[{part}]"
            else:
                out += f".{part}"
        return out
    
    
    def basic_checks(self, cfg):
        errors = []
        if not isinstance(cfg, dict):
            return ["$: config root must be an object"]
    
        for key in ("technology_bindings",):
            if key not in cfg:
                errors.append(f"$: missing required key '{key}'")
    
        if "config_version" in cfg:
            config_version = cfg.get("config_version")
            if not isinstance(config_version, int):
                errors.append("$.config_version: must be an integer")
            elif config_version != 2:
                errors.append("$.config_version: unsupported version (expected 2)")
    
        clients = cfg.get("download_clients")
        if clients is not None and not isinstance(clients, dict):
            errors.append("$.download_clients: must be an object")
    
        adapter_hooks = cfg.get("adapter_hooks")
        if adapter_hooks is not None and not isinstance(adapter_hooks, dict):
            errors.append("$.adapter_hooks: must be an object")
        if not isinstance(adapter_hooks, dict):
            adapter_hooks = {}
    
        bindings = cfg.get("technology_bindings")
        if bindings is not None and not isinstance(bindings, dict):
            errors.append("$.technology_bindings: must be an object")
            bindings = {}
        if not isinstance(bindings, dict):
            bindings = {}
    
        disallowed_adapter_hook_keys = (
            "technology_aliases",
            "adapter_classes",
            "download_client_adapter_classes",
            "media_server_adapter_classes",
            "before_common_steps",
            "app_service_classes",
            "service_technology_map",
        )
        for disallowed_key in disallowed_adapter_hook_keys:
            value = adapter_hooks.get(disallowed_key)
            if value not in (None, {}):
                errors.append(
                    f"$.adapter_hooks.{disallowed_key}: unsupported. "
                    "Move adapter/service registration into plugin manifests."
                )
    
        def _bound_key(name: str) -> str:
            return str(bindings.get(name, "") or "").strip().lower()
    
        torrent_client_key = _bound_key("torrent_client")
        usenet_client_key = _bound_key("usenet_client")
        media_server_key = _bound_key("media_server")
        request_manager_key = _bound_key("request_manager")
        if not media_server_key:
            errors.append("$.technology_bindings.media_server: required non-empty string")
        if "request_manager" in bindings:
            if not isinstance(bindings.get("request_manager"), str):
                errors.append("$.technology_bindings.request_manager: must be a string")
            elif not request_manager_key:
                errors.append(
                    "$.technology_bindings.request_manager: required non-empty string when set"
                )
    
        if isinstance(clients, dict):
            for name in (torrent_client_key, usenet_client_key):
                if not name:
                    continue
                if name not in clients:
                    errors.append(f"$.download_clients: missing active client section '{name}'")
    
        if isinstance(adapter_hooks, dict):
            legacy_hook_map = adapter_hooks.get("operation_handlers")
            if legacy_hook_map is not None:
                if not isinstance(legacy_hook_map, dict):
                    errors.append("$.adapter_hooks.operation_handlers: must be an object")
                else:
                    for impl, spec in legacy_hook_map.items():
                        path = f"$.adapter_hooks.operation_handlers.{impl}"
                        if spec in (None, ""):
                            continue
                        if ":" not in str(spec):
                            errors.append(
                                f"{path}: invalid hook spec '{spec}' (expected module.submodule:Symbol)"
                            )
    
            event_hook_map = adapter_hooks.get("event_handlers")
            if event_hook_map is not None:
                if not isinstance(event_hook_map, dict):
                    errors.append("$.adapter_hooks.event_handlers: must be an object")
                else:
                    from media_stack.services.enums import RunnerEvent
    
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
                            path = f"{event_path}.{impl}"
                            if spec in (None, ""):
                                continue
                            if ":" not in str(spec):
                                errors.append(
                                    f"{path}: invalid hook spec '{spec}' "
                                    "(expected module.submodule:Symbol)"
                                )
    
            _validate_media_server_operation_plans(
                adapter_hooks.get("media_server_event_plans")
                or adapter_hooks.get("media_server_operation_plans"),
                "$.adapter_hooks.media_server_operation_plans",
                errors,
            )
    
        media_server_cfg = cfg.get("media_server")
        if media_server_cfg is not None and not isinstance(media_server_cfg, dict):
            errors.append("$.media_server: must be an object")
        if isinstance(media_server_cfg, dict):
            _validate_media_server_operation_plans(
                media_server_cfg.get("operation_plans"),
                "$.media_server.operation_plans",
                errors,
            )
    
        rebuild_hooks = adapter_hooks.get("rebuild")
        if rebuild_hooks is not None and not isinstance(rebuild_hooks, dict):
            errors.append("$.adapter_hooks.rebuild: must be an object")
    
        microk8s_reconcile_hooks = adapter_hooks.get("microk8s_reconcile")
        if microk8s_reconcile_hooks is not None and not isinstance(microk8s_reconcile_hooks, dict):
            errors.append("$.adapter_hooks.microk8s_reconcile: must be an object")
        if isinstance(microk8s_reconcile_hooks, dict):
            _validate_microk8s_reconcile_hooks(
                microk8s_reconcile_hooks,
                "$.adapter_hooks.microk8s_reconcile",
                errors,
            )
    
        overlays = cfg.get("config_overlays")
        if overlays is not None:
            if not isinstance(overlays, dict):
                errors.append("$.config_overlays: must be an object")
            else:
                for key in ("enabled",):
                    if key in overlays and not isinstance(overlays.get(key), bool):
                        errors.append(f"$.config_overlays.{key}: must be a boolean")
                for key in ("env", "base_path", "overlay_dir"):
                    if key in overlays and not isinstance(overlays.get(key), str):
                        errors.append(f"$.config_overlays.{key}: must be a string")
                if "env_overlays" in overlays and not isinstance(overlays.get("env_overlays"), dict):
                    errors.append("$.config_overlays.env_overlays: must be an object")
    
        return errors
    
    
    @staticmethod
    def _validate_media_server_operation_plans(plans, path_prefix, errors):
        if plans is None:
            return
        if not isinstance(plans, dict):
            errors.append(f"{path_prefix}: must be an object")
            return
        for backend, phase_map in plans.items():
            backend_path = f"{path_prefix}.{backend}"
            if not isinstance(phase_map, dict):
                errors.append(f"{backend_path}: must be an object")
                continue
            for phase_name, phase_cfg in phase_map.items():
                phase_path = f"{backend_path}.{phase_name}"
                steps = phase_cfg
                if isinstance(phase_cfg, dict):
                    steps = phase_cfg.get("steps")
                if steps is None:
                    continue
                if not isinstance(steps, list):
                    errors.append(f"{phase_path}.steps: must be an array")
                    continue
                for idx, step in enumerate(steps):
                    step_path = f"{phase_path}.steps[{idx}]"
                    if not isinstance(step, dict):
                        errors.append(f"{step_path}: must be an object")
                        continue
                    event_name = str(step.get("event") or "").strip()
                    handler = str(step.get("handler") or "").strip()
                    operation = str(step.get("operation") or "").strip()
                    if not handler and operation:
                        handler = operation
                    if not event_name and operation:
                        event_name = "RUN"
                    if not handler:
                        errors.append(f"{step_path}.handler: required non-empty string")
                        errors.append(f"{step_path}.operation: required non-empty string")
                    if event_name:
                        from media_stack.services.enums import RunnerEvent
    
                        try:
                            RunnerEvent.from_value(event_name)
                        except ValueError:
                            errors.append(
                                f"{step_path}.event: unsupported event '{event_name}' "
                                f"(expected one of {', '.join(RunnerEvent.choices())})"
                            )
    
    
    @staticmethod
    def _validate_microk8s_reconcile_hooks(hooks, path_prefix, errors):
        phase_plan = hooks.get("phase_plan")
        if not isinstance(phase_plan, list) or not phase_plan:
            errors.append(f"{path_prefix}.phase_plan: must be a non-empty array")
        else:
            from media_stack.services.enums import RunnerEvent
    
            for idx, step in enumerate(phase_plan):
                step_path = f"{path_prefix}.phase_plan[{idx}]"
                if not isinstance(step, dict):
                    errors.append(f"{step_path}: must be an object")
                    continue
                handler = str(step.get("handler") or "").strip()
                event_name = str(step.get("event") or "").strip()
                if not handler:
                    errors.append(f"{step_path}.handler: required non-empty string")
                if not event_name:
                    errors.append(f"{step_path}.event: required non-empty string")
                else:
                    try:
                        RunnerEvent.from_value(event_name)
                    except ValueError:
                        errors.append(
                            f"{step_path}.event: unsupported event '{event_name}' "
                            f"(expected one of {', '.join(RunnerEvent.choices())})"
                        )
    
        optional_deployments = hooks.get("optional_deployments")
        if optional_deployments is not None and not isinstance(optional_deployments, list):
            errors.append(f"{path_prefix}.optional_deployments: must be an array")
    
        optional_manifest_paths = hooks.get("optional_manifest_paths")
        if optional_manifest_paths is not None and not isinstance(optional_manifest_paths, list):
            errors.append(f"{path_prefix}.optional_manifest_paths: must be an array")
    
        conditional_manifests = hooks.get("conditional_manifests")
        if conditional_manifests is not None and not isinstance(conditional_manifests, list):
            errors.append(f"{path_prefix}.conditional_manifests: must be an array")
        if isinstance(conditional_manifests, list):
            for idx, item in enumerate(conditional_manifests):
                item_path = f"{path_prefix}.conditional_manifests[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{item_path}: must be an object")
                    continue
                if not str(item.get("deployment") or "").strip():
                    errors.append(f"{item_path}.deployment: required non-empty string")
                if not str(item.get("manifest_path") or "").strip():
                    errors.append(f"{item_path}.manifest_path: required non-empty string")
    
    
    def main(self) -> int:
        parser = argparse.ArgumentParser(
            prog="bin/validate-bootstrap-config.sh",
            description="Validate media-stack bootstrap config",
        )
        parser.add_argument(
            "--config",
            default="contracts/media-stack.config.json",
            help="Path to bootstrap config JSON",
        )
        parser.add_argument(
            "--schema",
            default="",
            help="Path to JSON schema (optional)",
        )
        args = parser.parse_args()
    
        config_path = Path(args.config)
        schema_path = Path(args.schema) if args.schema else None
    
        if config_path.is_file():
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            cfg = {}
    
        if not schema_path or not schema_path.exists():
            schema = None
        else:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
    
        if schema:
            try:
                import jsonschema  # type: ignore
            except ModuleNotFoundError:
                print(
                    "[WARN] jsonschema module not available, skipping schema validation.",
                    file=sys.stderr,
                )
                schema = None
    
        if schema:
            validator = jsonschema.Draft202012Validator(schema)
            errors = sorted(validator.iter_errors(cfg), key=lambda err: list(err.path))
            if errors:
                print("[ERR] Bootstrap config schema validation failed:", file=sys.stderr)
                for err in errors:
                    path = format_path(list(err.path))
                    print(f"  - {path}: {err.message}", file=sys.stderr)
                return 1
    
        from media_stack.services.top_level_config_model import TopLevelBootstrapConfig
    
        try:
            TopLevelBootstrapConfig.from_dict(cfg)
        except ValueError as exc:
            print(f"[ERR] Bootstrap config semantic validation failed: {exc}", file=sys.stderr)
            return 1
    
        print(f"[OK] Bootstrap config is schema-valid: {config_path} (schema={schema_path})")
        return 0
    
    


_instance = ValidateControllerConfigCommand()
format_path = _instance.format_path
basic_checks = _instance.basic_checks
main = _instance.main
_validate_media_server_operation_plans = _instance._validate_media_server_operation_plans
_validate_microk8s_reconcile_hooks = _instance._validate_microk8s_reconcile_hooks

if __name__ == "__main__":
    raise SystemExit(main())
