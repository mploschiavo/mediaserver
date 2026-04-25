#!/usr/bin/env python3
"""Python CLI for deploy-stack orchestration.

This is the entry point module. The DeployStackRunner class is composed from
three mixin modules extracted for maintainability:

- deploy_stack_config_resolution — config/hook resolution methods
- deploy_stack_runner_services  — service factories, artifacts, utilities
- deploy_stack_runner_phases    — run() orchestration, validation, phase actions
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from media_stack.core.phase_tracker import PhaseTracker
from media_stack.core.platform_adapter import RebuildPlatformAdapter
from media_stack.core.platform_plugin_contract import PlatformPlugin

from media_stack.cli.commands.deploy_stack_errors import (
    DeployError,
    SkipPhase,
    _MIN_STACK_DISK_ALLOCATION_GB,
)
from media_stack.cli.commands.deploy_stack_config_resolution import (
    ConfigResolutionMixin,
)
from media_stack.cli.commands.deploy_stack_runner_services import (
    RunnerServicesMixin,
)
from media_stack.cli.commands.deploy_stack_runner_phases import (
    RunnerPhasesMixin,
)
from media_stack.core.cli_common import info, warn
from media_stack.cli.workflows.deploy_cli_config_service import (
    DeployStackConfig,
    parse_deploy_stack_config,
)


@dataclass
class DeployStackRunner(ConfigResolutionMixin, RunnerServicesMixin, RunnerPhasesMixin):
    cfg: DeployStackConfig
    kube: Any | None = None
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))
    backup_secret_values: dict[str, str] = field(default_factory=dict)
    info_fn: Callable[[str], None] = info
    _resolved_config_cache: dict[str, object] | None = field(default=None, init=False, repr=False)
    _platform_adapter_cache: RebuildPlatformAdapter | None = field(
        default=None, init=False, repr=False
    )
    _platform_plugin_cache: PlatformPlugin | None = field(default=None, init=False, repr=False)
    _platform_client_cache: dict[str, object] = field(default_factory=dict, init=False, repr=False)
    runtime_artifacts_root: Path | None = field(default=None, init=False, repr=False)
    _k8s_manifest_capture_counter: int = field(default=0, init=False, repr=False)
    _delete_environment_enabled_cache: bool | None = field(default=None, init=False, repr=False)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root_dir = Path(__file__).resolve().parents[2]
    cfg = parse_deploy_stack_config(args, root_dir=root_dir)
    runner = DeployStackRunner(cfg=cfg)
    try:
        return runner.run()
    except Exception as exc:
        warn(f"Deploy/bootstrap failed: {exc}")
        try:
            runner.emit_failure_status_snapshot()
        except Exception as snapshot_exc:
            warn(f"Failed collecting failure status snapshot: {snapshot_exc}")
        runner.tracker.summary()
        runner.notify(
            "error",
            f"media-stack deploy/bootstrap failed (profile={cfg.profile}, namespace={cfg.namespace})",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
