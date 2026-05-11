#!/usr/bin/env python3
"""Python CLI for deploy-stack orchestration.

This is the entry point module. The DeployStackRunner class is composed from
the workflows-tier DeployConfigService plus two mixin modules:

- ``workflows.deploy_config_service.DeployConfigService`` — config / hook
  resolution (the single resolver established by ADR-0015 Phase 3;
  pre-Phase-3 this was a ConfigResolutionMixin co-located with
  the runner here in commands/, which duplicated logic with the
  parallel ``workflows.deploy_hook_config_resolver`` and caused the
  2026-05-11 deploy-CLI bug chain to keep surfacing new bugs).
- ``deploy_stack_runner_services``  — service factories, artifacts,
  utilities (kept as mixin under ADR-0015 Phase 4 scope).
- ``deploy_stack_runner_phases``    — ``run()`` orchestration,
  validation, phase actions (kept as mixin under ADR-0015 Phase 4).

Per ADR-0012, the module-level ``main`` entry point is bound to a
singleton :class:`DeployStackMainEntryPoint`. Tests
(``tests/unit/adapters/test_rebuild_and_bootstrap_main.py``,
``tests/unit/adapters/test_deploy_stack_main.py``) ``mock.patch`` the
module-level ``parse_deploy_stack_config`` / ``DeployStackRunner`` /
``warn`` names; the entry-point method dispatches through
``sys.modules[__name__]`` so the patches keep intercepting.
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
from media_stack.cli.workflows.deploy_config_service import (
    DeployConfigService,
)


@dataclass
class DeployStackRunner(RunnerServicesMixin, RunnerPhasesMixin):
    cfg: DeployStackConfig
    kube: Any | None = None
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))
    backup_secret_values: dict[str, str] = field(default_factory=dict)
    info_fn: Callable[[str], None] = info
    config_service: DeployConfigService = field(init=False, repr=False)
    _platform_adapter_cache: RebuildPlatformAdapter | None = field(
        default=None, init=False, repr=False
    )
    _platform_plugin_cache: PlatformPlugin | None = field(default=None, init=False, repr=False)
    _platform_client_cache: dict[str, object] = field(default_factory=dict, init=False, repr=False)
    runtime_artifacts_root: Path | None = field(default=None, init=False, repr=False)
    _k8s_manifest_capture_counter: int = field(default=0, init=False, repr=False)
    _delete_environment_enabled_cache: bool | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # Composition: DeployConfigService is the workflows-tier
        # resolver for every "what should this deploy actually do?"
        # question. The runner used to inherit a ConfigResolutionMixin
        # for this; ADR-0015 Phase 3 collapsed the mixin into this
        # service to eliminate the parallel-resolver bug class.
        self.config_service = DeployConfigService(self.cfg)


class DeployStackMainEntryPoint:
    """CLI entry point for ``bin/ops/deploy-stack``.

    Builds a :class:`DeployStackRunner` from parsed config and
    delegates to ``runner.run()``. On failure, captures a status
    snapshot, prints a tracker summary, and emits an error
    notification.

    Dispatches through ``sys.modules[__name__]`` so test patches
    against ``parse_deploy_stack_config`` / ``DeployStackRunner`` /
    ``warn`` keep intercepting the names this method calls.
    """

    def main(self, argv: list[str] | None = None) -> int:
        """Run the deploy-stack CLI. Returns process exit code."""
        module = sys.modules[__name__]
        args = argv if argv is not None else sys.argv[1:]
        # parents[4] = repo root (this file at src/media_stack/cli/commands/...).

        # Pre-ADR-0001-Phase-12 the CLI lived at scripts/cli/ where parents[2] was

        # repo-root; after the move to src/media_stack/cli/commands/ the value was

        # never updated, landing at src/media_stack/ and silently breaking every

        # root_dir / "contracts" / … lookup. Matches the parents[4] used by

        # teardown_stack_main, release_pipeline_main, apply_scale_policy_main,

        # dup_burndown_main, run_unit_tests_main.

        root_dir = Path(__file__).resolve().parents[4]
        cfg = module.parse_deploy_stack_config(args, root_dir=root_dir)
        runner = module.DeployStackRunner(cfg=cfg)
        try:
            return runner.run()
        except Exception as exc:
            module.warn(f"Deploy/bootstrap failed: {exc}")
            try:
                runner.emit_failure_status_snapshot()
            except Exception as snapshot_exc:
                module.warn(
                    f"Failed collecting failure status snapshot: {snapshot_exc}"
                )
            runner.tracker.summary()
            runner.notify(
                "error",
                f"media-stack deploy/bootstrap failed (profile={cfg.profile}, namespace={cfg.namespace})",
            )
            return 1


_INSTANCE = DeployStackMainEntryPoint()
main = _INSTANCE.main


__all__ = [
    "DeployError",
    "DeployStackMainEntryPoint",
    "DeployStackRunner",
    "SkipPhase",
    "_MIN_STACK_DISK_ALLOCATION_GB",
    "main",
    "parse_deploy_stack_config",
    "warn",
]


if __name__ == "__main__":
    raise SystemExit(main())
