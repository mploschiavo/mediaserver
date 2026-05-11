"""ValidateControllerConfigService — Composition Root for the validate workflow.

ADR-0015 Phase 7b. Pre-Phase-7b the validation cascade lived
inline inside :meth:`ValidateControllerConfigCommand.main` —
schema validation (jsonschema) + semantic validation
(:class:`TopLevelBootstrapConfig`.from_dict) + the
``basic_checks`` helper. Phase 7b moves all three into this
Composition Root so the argparse entry point becomes a
2-call shim:

    cfg = service.load_config(args.config)
    return service.validate(cfg, schema_path=args.schema)

Each validator is constructor-injectable for testability.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from media_stack.cli.workflows.validate_controller_config.basic_config_validator import (
    BasicConfigValidator,
)
from media_stack.cli.workflows.validate_controller_config.path_formatter import (
    JsonPathFormatter,
)


class ValidateControllerConfigService:
    """Composition Root: schema validation + semantic validation.

    The order matters: schema validation runs first because it
    catches mis-typed JSON shapes that would crash the semantic
    validator's :func:`TopLevelBootstrapConfig.from_dict` with
    an opaque ``KeyError``. Once the schema check passes, semantic
    validation surfaces business-rule errors with operator-friendly
    messages.
    """

    def __init__(
        self,
        *,
        basic_validator: BasicConfigValidator | None = None,
        path_formatter: JsonPathFormatter | None = None,
    ) -> None:
        self._basic_validator = basic_validator or BasicConfigValidator()
        self._path_formatter = path_formatter or JsonPathFormatter()

    def basic_checks(self, cfg: object) -> list[str]:
        """Expose the basic-checks pipeline for direct test access."""
        return self._basic_validator.check(cfg)

    def load_config(self, config_path: Path) -> dict:
        if not config_path.is_file():
            return {}
        return json.loads(config_path.read_text(encoding="utf-8"))

    def load_schema(self, schema_path: Path | None) -> dict | None:
        if schema_path is None or not schema_path.exists():
            return None
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def validate(
        self,
        cfg: object,
        *,
        config_path: Path,
        schema_path: Path | None,
    ) -> int:
        schema = self.load_schema(schema_path)
        if schema is not None:
            schema = self._resolve_jsonschema_available(schema)
        if schema is not None:
            rc = self._run_schema_validation(cfg, schema)
            if rc != 0:
                return rc
        return self._run_semantic_validation(cfg, config_path, schema_path)

    def _resolve_jsonschema_available(self, schema: dict) -> dict | None:
        try:
            import jsonschema  # type: ignore  # noqa: F401
        except ModuleNotFoundError:
            print(
                "[WARN] jsonschema module not available, skipping schema validation.",
                file=sys.stderr,
            )
            return None
        return schema

    def _run_schema_validation(self, cfg: object, schema: dict) -> int:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(cfg), key=lambda err: list(err.path))
        if not errors:
            return 0
        print("[ERR] Bootstrap config schema validation failed:", file=sys.stderr)
        for err in errors:
            path = self._path_formatter.format(list(err.path))
            print(f"  - {path}: {err.message}", file=sys.stderr)
        return 1

    def _run_semantic_validation(
        self, cfg: object, config_path: Path, schema_path: Path | None,
    ) -> int:
        from media_stack.services.top_level_config_model import TopLevelBootstrapConfig

        try:
            TopLevelBootstrapConfig.from_dict(cfg)
        except ValueError as exc:
            print(
                f"[ERR] Bootstrap config semantic validation failed: {exc}",
                file=sys.stderr,
            )
            return 1
        print(
            f"[OK] Bootstrap config is schema-valid: {config_path} (schema={schema_path})"
        )
        return 0


__all__ = ["ValidateControllerConfigService"]
