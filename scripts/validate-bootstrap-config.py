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
    if isinstance(clients, dict):
        for name in ("qbittorrent", "sabnzbd"):
            if name not in clients:
                errors.append(f"$.download_clients: missing '{name}' section")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate media-stack bootstrap config")
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
