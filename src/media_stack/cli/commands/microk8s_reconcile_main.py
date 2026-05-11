#!/usr/bin/env python3
"""Entry-point shim for ``bin/k8s/microk8s-reconcile.sh``.

ADR-0015 Phase 7a. Pre-Phase-7a this module held the
``Microk8sReconcileRunner`` god class (~165 LoC, 11 methods) +
``Microk8sReconcileCommand`` config-parser + four frozen
dataclasses, all under ``cli/commands/`` even though they're
workflow orchestration. Phase 7a moved everything except the
argparse + exit-code wiring into
:mod:`media_stack.cli.workflows.microk8s_reconcile`.

The module-level aliases (``parse_config``, ``main``,
``_load_reconcile_hooks``, ``_parse_phase_plan``) survive for the
historical test-patch surface; each forwards into the workflows
sub-package via the singleton :class:`Microk8sReconcileEntryPoint`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_stack.cli.workflows.microk8s_reconcile import (
    ConditionalManifestRule,
    Microk8sReconcileConfig,
    Microk8sReconcileService,
    Microk8sReconcileState,
    ReconcileConfigLoader,
    ReconcilePhaseStep,
)
from media_stack.core.cli_common import repo_root_from_script_file
from media_stack.core.exceptions import ConfigError, MediaStackError


class Microk8sReconcileEntryPoint:
    """Per-ADR-0012 entry-point: argv → cfg → service.run → exit code."""

    def __init__(self) -> None:
        self._loader = ReconcileConfigLoader()

    def parse_config(self, argv: list[str] | None = None) -> Microk8sReconcileConfig:
        parser = argparse.ArgumentParser(
            prog="bin/microk8s-reconcile.sh",
            description="Reconcile manifests from config-defined RECONCILE phase plan.",
        )
        parser.add_argument("--include-optional", action="store_true", default=False)
        args = parser.parse_args(argv)
        root_dir = repo_root_from_script_file(__file__)
        return self._loader.build_config(
            root_dir=root_dir,
            include_optional=bool(args.include_optional),
        )

    def load_reconcile_hooks(self, config_file: Path) -> dict[str, object]:
        return self._loader.load_reconcile_hooks(config_file)

    def parse_phase_plan(self, raw_plan: object) -> tuple[ReconcilePhaseStep, ...]:
        return self._loader.parse_phase_plan(raw_plan)

    def main(self, argv: list[str] | None = None) -> int:
        module = sys.modules[__name__]
        try:
            cfg = module.parse_config(argv)
            return Microk8sReconcileService(cfg).run()
        except (ConfigError, MediaStackError, OSError, ValueError) as exc:
            print(f"[ERR] {exc}", file=sys.stderr)
            return 1


_INSTANCE = Microk8sReconcileEntryPoint()
parse_config = _INSTANCE.parse_config
_load_reconcile_hooks = _INSTANCE.load_reconcile_hooks
_parse_phase_plan = _INSTANCE.parse_phase_plan
main = _INSTANCE.main


__all__ = [
    "ConditionalManifestRule",
    "Microk8sReconcileConfig",
    "Microk8sReconcileEntryPoint",
    "Microk8sReconcileState",
    "ReconcilePhaseStep",
    "_load_reconcile_hooks",
    "_parse_phase_plan",
    "main",
    "parse_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
