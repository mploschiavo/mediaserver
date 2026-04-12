#!/usr/bin/env python3
"""Validate deployment bootstrap profile YAML against schema + semantic model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from media_stack.core.controller_profile import ControllerProfileConfig




class ValidateControllerProfileCommand:
    """Wraps profile validation CLI entrypoint."""

    def main(self) -> int:
        parser = argparse.ArgumentParser(
            prog="bin/validate-bootstrap-profile.sh",
            description="Validate media-stack bootstrap profile YAML",
        )
        parser.add_argument("--config", default="contracts/media-stack.profile.yaml")
        parser.add_argument("--schema", default="contracts/media-stack.profile.schema.json")
        args = parser.parse_args()

        config_path = Path(args.config)
        schema_path = Path(args.schema)

        if not config_path.exists():
            print(f"[ERR] Bootstrap profile not found: {config_path}", file=sys.stderr)
            return 2
        if not schema_path.exists():
            print(f"[ERR] Bootstrap profile schema not found: {schema_path}", file=sys.stderr)
            return 2

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            print("[ERR] Bootstrap profile root must be an object", file=sys.stderr)
            return 1

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        try:
            import jsonschema  # type: ignore
        except ModuleNotFoundError:
            print("[ERR] jsonschema module is required for strict validation.", file=sys.stderr)
            return 2

        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
        if errors:
            print("[ERR] Bootstrap profile schema validation failed:", file=sys.stderr)
            for err in errors:
                print(f"  - {_format_path(list(err.path))}: {err.message}", file=sys.stderr)
            return 1

        try:
            ControllerProfileConfig.from_dict(payload, source_path=config_path)
        except ValueError as exc:
            print(f"[ERR] Bootstrap profile semantic validation failed: {exc}", file=sys.stderr)
            return 1

        print(f"[OK] Bootstrap profile is valid: {config_path} (schema={schema_path})")
        return 0


    @staticmethod
    def _format_path(path_parts: list[object]) -> str:
        if not path_parts:
            return "$"
        out = "$"
        for part in path_parts:
            if isinstance(part, int):
                out += f"[{part}]"
            else:
                out += f".{part}"
        return out


_instance = ValidateControllerProfileCommand()
main = _instance.main

if __name__ == "__main__":
    raise SystemExit(main())
_format_path = _instance._format_path
