#!/usr/bin/env python3
"""Entry-point shim for ``bin/apply-scale-policy.sh``.

ADR-0015 Phase 7i. Pre-Phase-7i this module held the full
``ApplyScalePolicyCommand`` (177 LoC, 5 ``@staticmethod`` violators).
Phase 7i moved the workflow onto :class:`ApplyScalePolicyRunner`
under workflows/ with proper instance methods (no ``@staticmethod``);
what remains here is argparse + main + back-compat aliases.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from media_stack.cli.workflows.apply_scale_policy_runner import (
    ApplyScalePolicyRunner,
)


class ApplyScalePolicyEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = ApplyScalePolicyRunner()

    @property
    def runner(self) -> ApplyScalePolicyRunner:
        return self._runner

    def build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="bin/apply-scale-policy.sh",
            description=(
                "Enforce scale policy: keep managed apps at replicas>=1 "
                "and optionally scale configured apps to 0."
            ),
        )
        parser.add_argument(
            "config_file",
            nargs="?",
            default=str(self._runner.default_config_file()),
            help="Bootstrap config JSON path",
        )
        parser.add_argument(
            "--namespace", default=os.environ.get("NAMESPACE", "media-stack"),
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--scale-to-zero",
            dest="scale_to_zero",
            action="store_true",
            default=self._runner.env_truthy(os.environ.get("SCALE_TO_ZERO")),
            help=(
                "Scale apps listed in adapter_hooks.scale_policy.scale_to_zero_apps "
                "to 0 replicas."
            ),
        )
        return parser

    def main(self, argv: list[str] | None = None) -> int:
        args = self.build_arg_parser().parse_args(argv)
        return self._runner.run(
            config_file=Path(str(args.config_file)).resolve(),
            namespace=str(args.namespace or "").strip(),
            dry_run=bool(args.dry_run),
            scale_to_zero=bool(args.scale_to_zero),
        )


# Module-level singleton + back-compat aliases.
_INSTANCE = ApplyScalePolicyEntryPoint()
_RUNNER = _INSTANCE.runner
build_arg_parser = _INSTANCE.build_arg_parser
main = _INSTANCE.main
_current_replicas = _RUNNER.current_replicas
_default_config_file = _RUNNER.default_config_file
_deployment_exists = _RUNNER.deployment_exists
_env_truthy = _RUNNER.env_truthy
_scale_deployment = _RUNNER.scale_deployment


__all__ = [
    "ApplyScalePolicyEntryPoint",
    "_current_replicas",
    "_default_config_file",
    "_deployment_exists",
    "_env_truthy",
    "_scale_deployment",
    "build_arg_parser",
    "main",
]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — top-level CLI catch
        print(f"[ERR] {exc}", file=sys.stderr)
        sys.exit(1)
