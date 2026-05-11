#!/usr/bin/env python3
"""Entry-point shim for ``bin/validate-bootstrap-config.sh``.

ADR-0015 Phase 7b. Pre-Phase-7b this module held the 365-LoC
``ValidateControllerConfigCommand`` god class (5 methods + 2
``@staticmethod`` helpers) covering JSON-path formatting, basic
config checks, two event-plan strategies, schema validation, and
semantic validation. Phase 7b moved everything except argparse +
exit-code wiring into
:mod:`media_stack.cli.workflows.validate_controller_config`.

The module-level aliases (``format_path``, ``basic_checks``,
``_validate_media_server_operation_plans``, ``main``) survive for
the historical test surface (``test_cli_commands_extended.py``).
Each is bound to a method on a singleton
:class:`ValidateControllerConfigEntryPoint` per ADR-0012.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_stack.cli.workflows.validate_controller_config import (
    BasicConfigValidator,
    JsonPathFormatter,
    MediaServerOperationPlanValidator,
    Microk8sReconcileHookValidator,
    ValidateControllerConfigService,
)


class ValidateControllerConfigEntryPoint:
    """Per-ADR-0012 entry-point: argv → cfg → service.validate → exit code."""

    def __init__(self) -> None:
        self._service = ValidateControllerConfigService()
        self._path_formatter = JsonPathFormatter()
        self._basic_validator = BasicConfigValidator()

    def format_path(self, path_parts) -> str:
        return self._path_formatter.format(path_parts)

    def basic_checks(self, cfg) -> list[str]:
        return self._basic_validator.check(cfg)

    def validate_media_server_operation_plans(
        self, plans, path_prefix, errors,
    ) -> None:
        MediaServerOperationPlanValidator(errors).validate(plans, path_prefix)

    def validate_microk8s_reconcile_hooks(
        self, hooks, path_prefix, errors,
    ) -> None:
        Microk8sReconcileHookValidator(errors).validate(hooks, path_prefix)

    def main(self, argv: list[str] | None = None) -> int:
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
        args = parser.parse_args(argv)

        config_path = Path(args.config)
        schema_path = Path(args.schema) if args.schema else None
        cfg = self._service.load_config(config_path)
        return self._service.validate(
            cfg, config_path=config_path, schema_path=schema_path,
        )


_INSTANCE = ValidateControllerConfigEntryPoint()
format_path = _INSTANCE.format_path
basic_checks = _INSTANCE.basic_checks
_validate_media_server_operation_plans = _INSTANCE.validate_media_server_operation_plans
_validate_microk8s_reconcile_hooks = _INSTANCE.validate_microk8s_reconcile_hooks
main = _INSTANCE.main


__all__ = [
    "ValidateControllerConfigEntryPoint",
    "_validate_media_server_operation_plans",
    "_validate_microk8s_reconcile_hooks",
    "basic_checks",
    "format_path",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
