#!/usr/bin/env python3
"""Validate bootstrap config against schema (with clear errors)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def format_path(path_parts):
    if not path_parts:
        return "$"
    out = "$"
    for part in path_parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}"
    return out


def basic_checks(cfg):
    errors = []
    if not isinstance(cfg, dict):
        return ["$: config root must be an object"]

    for key in ("prowlarr_url", "arr_apps", "download_clients"):
        if key not in cfg:
            errors.append(f"$: missing required key '{key}'")

    arr_apps = cfg.get("arr_apps")
    if arr_apps is not None and not isinstance(arr_apps, list):
        errors.append("$.arr_apps: must be an array")
    if isinstance(arr_apps, list):
        for idx, app in enumerate(arr_apps):
            if not isinstance(app, dict):
                errors.append(f"$.arr_apps[{idx}]: must be an object")
                continue
            for required in ("name", "implementation", "url", "root_folder"):
                if not str(app.get(required) or "").strip():
                    errors.append(f"$.arr_apps[{idx}].{required}: required non-empty string")

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

    aliases_map = adapter_hooks.get("technology_aliases")
    aliases: dict[str, str] = {}
    if aliases_map is not None:
        if not isinstance(aliases_map, dict):
            errors.append("$.adapter_hooks.technology_aliases: must be an object")
        else:
            for src, dst in aliases_map.items():
                src_key = str(src or "").strip().lower()
                dst_key = str(dst or "").strip().lower()
                if not src_key or not dst_key:
                    errors.append(
                        "$.adapter_hooks.technology_aliases: keys and values must be non-empty strings"
                    )
                    continue
                aliases[src_key] = dst_key

    default_bindings = adapter_hooks.get("default_bindings")
    if default_bindings is not None and not isinstance(default_bindings, dict):
        errors.append("$.adapter_hooks.default_bindings: must be an object")
        default_bindings = {}
    if not isinstance(default_bindings, dict):
        default_bindings = {}

    def _bound_key(name: str, default: str) -> str:
        default_value = str(default_bindings.get(name, default) or "").strip().lower() or default
        value = str(bindings.get(name, default_value) or "").strip().lower() or default_value
        return aliases.get(value, value)

    torrent_client_key = _bound_key("torrent_client", "qbittorrent")
    usenet_client_key = _bound_key("usenet_client", "sabnzbd")

    if isinstance(clients, dict):
        for name in (torrent_client_key, usenet_client_key):
            if name not in clients:
                errors.append(f"$.download_clients: missing active client section '{name}'")

    if isinstance(adapter_hooks, dict):
        for hook_key in (
            "before_common_steps",
            "adapter_classes",
            "download_client_adapter_classes",
            "media_server_adapter_classes",
            "app_service_classes",
            "operation_handlers",
        ):
            hook_map = adapter_hooks.get(hook_key)
            if hook_map is None:
                continue
            if not isinstance(hook_map, dict):
                errors.append(f"$.adapter_hooks.{hook_key}: must be an object")
                continue
            for impl, spec in hook_map.items():
                path = f"$.adapter_hooks.{hook_key}.{impl}"
                if spec in (None, ""):
                    continue
                if ":" not in str(spec):
                    errors.append(
                        f"{path}: invalid hook spec '{spec}' (expected module.submodule:Symbol)"
                    )

        if isinstance(default_bindings, dict):
            for role in ("torrent_client", "usenet_client", "media_server"):
                if role not in default_bindings:
                    continue
                token = str(default_bindings.get(role) or "").strip()
                if not token:
                    errors.append(
                        f"$.adapter_hooks.default_bindings.{role}: required non-empty string"
                    )

        _validate_media_server_operation_plans(
            adapter_hooks.get("media_server_operation_plans"),
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

    return errors


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
                operation = str(step.get("operation") or "").strip()
                if not operation:
                    errors.append(f"{step_path}.operation: required non-empty string")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/validate-bootstrap-config.sh",
        description="Validate media-stack bootstrap config",
    )
    parser.add_argument(
        "--config",
        default="bootstrap/media-stack.bootstrap.json",
        help="Path to bootstrap config JSON",
    )
    parser.add_argument(
        "--schema",
        default="bootstrap/media-stack.bootstrap.schema.json",
        help="Path to JSON schema",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    schema_path = Path(args.schema)

    if not config_path.exists():
        print(f"[ERR] Config not found: {config_path}", file=sys.stderr)
        return 2
    if not schema_path.exists():
        print(f"[ERR] Schema not found: {schema_path}", file=sys.stderr)
        return 2

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(cfg), key=lambda err: list(err.path))
        if errors:
            print("[ERR] Bootstrap config schema validation failed:", file=sys.stderr)
            for err in errors:
                path = format_path(list(err.path))
                print(f"  - {path}: {err.message}", file=sys.stderr)
            return 1
        print(f"[OK] Bootstrap config is schema-valid: {config_path} (schema={schema_path})")
        return 0
    except ModuleNotFoundError:
        fallback_errors = basic_checks(cfg)
        if fallback_errors:
            print(
                "[ERR] jsonschema module not installed and basic validation failed:",
                file=sys.stderr,
            )
            for line in fallback_errors:
                print(f"  - {line}", file=sys.stderr)
            return 1
        print(
            "[WARN] jsonschema module not installed; ran basic validation only. "
            "Install python3-jsonschema for full schema checks."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
