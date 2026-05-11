#!/usr/bin/env python3
"""Python CLI entry point for deploy-stack orchestration.

ADR-0015 Phase 4. Pre-Phase-4 this module held a 90-line
``DeployStackRunner`` dataclass composed via two mixins
(``RunnerServicesMixin``, ``RunnerPhasesMixin``) that lived in
``commands/deploy_stack_runner_phases.py`` and
``commands/deploy_stack_runner_services.py``. Phase 4 moved the
orchestration logic into the workflows tier under
``cli/workflows/deploy_orchestration/`` and split the mixins into
nine SRP classes wired through a Composition Root.

What remains here is the entry-point shim only:

* :class:`DeployStackRunner` — a thin subclass of
  :class:`DeployPipelineRunner` kept so existing tests that
  ``mock.patch("media_stack.cli.commands.deploy_stack_main.DeployStackRunner")``
  keep intercepting the call.
* :class:`DeployStackMainEntryPoint` — argv parsing + exit-code
  translation + at-failure notification.

Per ADR-0012, the module-level ``main`` is bound to a singleton
instance method. Tests (``tests/unit/adapters/test_rebuild_and_bootstrap_main.py``,
``tests/unit/adapters/test_deploy_stack_main.py``) ``mock.patch``
the module-level ``parse_deploy_stack_config`` / ``DeployStackRunner`` /
``warn`` names; the entry-point method dispatches through
``sys.modules[__name__]`` so the patches keep intercepting.
"""

from __future__ import annotations

import sys
from pathlib import Path

from media_stack.cli.workflows.deploy_cli_config_service import (
    DeployStackConfig,
    parse_deploy_stack_config,
)
from media_stack.cli.workflows.deploy_errors import (
    DeployError,
    SkipPhase,
    _MIN_STACK_DISK_ALLOCATION_GB,
)
from media_stack.cli.workflows.deploy_orchestration import DeployPipelineRunner
from media_stack.core.cli_common import info, warn


class DeployStackRunner(DeployPipelineRunner):
    """Thin commands-tier subclass kept for test-patch compatibility.

    The pipeline orchestration logic now lives on
    :class:`DeployPipelineRunner` under
    ``cli/workflows/deploy_orchestration``. ``DeployStackRunner``
    survives as a name in this module because the deploy-stack test
    suite patches ``media_stack.cli.commands.deploy_stack_main.DeployStackRunner``
    by qualified path; renaming or removing the name here would
    invalidate those patches. Removal queued for Phase 6.
    """


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
    "DeployStackConfig",
    "DeployStackMainEntryPoint",
    "DeployStackRunner",
    "SkipPhase",
    "_MIN_STACK_DISK_ALLOCATION_GB",
    "info",
    "main",
    "parse_deploy_stack_config",
    "warn",
]


if __name__ == "__main__":
    raise SystemExit(main())
