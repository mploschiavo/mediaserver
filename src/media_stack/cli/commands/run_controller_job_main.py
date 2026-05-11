#!/usr/bin/env python3
"""Entry-point shim for ``bin/k8s/run-controller-job.sh``.

ADR-0015 Phase 7c. Pre-Phase-7c this module held the 604-LoC
``RunBootstrapJobRunner`` god class (30+ methods, inherited from
the 75-LoC ``_RunBootstrapJobPrimingMixin``). Phase 7c moved all
of that into a new sub-package under workflows/ as six SRP
classes (Factory bundle / Repository / Strategy / two Command
sets / Composition Root + Template Method).

What remains here is the entry-point shim:

* :class:`RunBootstrapJobRunner` — a thin subclass of
  :class:`RunBootstrapJobPipeline` kept so existing tests that
  ``MODULE.RunBootstrapJobRunner(...)``-construct the class by
  qualified path keep working.
* :class:`RunBootstrapJobEntryPoint` — argv → cfg → pipeline.run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from media_stack.cli.workflows.run_controller_job_cli_config_service import (
    RunBootstrapJobConfig,
    parse_run_bootstrap_job_config,
)
from media_stack.cli.workflows.run_controller_job_orchestration import (
    RunBootstrapJobPipeline,
)
from media_stack.core.cli_common import PhaseTracker, err, info, ts, warn
from media_stack.core.exceptions import ConfigError, MediaStackError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


class RunBootstrapJobRunner(RunBootstrapJobPipeline):
    """Thin commands-tier subclass kept for test-patch compatibility.

    The pipeline orchestration logic now lives on
    :class:`RunBootstrapJobPipeline` under
    ``cli/workflows/run_controller_job_orchestration``. This name
    survives because the unit tests in
    ``tests/unit/jobs/test_run_bootstrap_job.py`` construct it via
    ``MODULE.RunBootstrapJobRunner(...)``; renaming would invalidate
    those tests' qualified-path access.
    """


class RunBootstrapJobEntryPoint:
    """Per-ADR-0012 entry-point: argv → cfg → runner.run → exit code."""

    def main(self, argv: list[str] | None = None) -> int:
        # parents[4] = repo root (this file at src/media_stack/cli/commands/...).
        # See deploy_stack_main / teardown_stack_main / release_pipeline_main
        # for the parents[4] rationale (pre-ADR-0001-Phase-12 layout was
        # scripts/cli/ where parents[2] resolved correctly).
        root_dir = Path(__file__).resolve().parents[4]
        cfg = parse_run_bootstrap_job_config(argv, root_dir=root_dir)
        runner = RunBootstrapJobRunner(
            cfg=cfg,
            kube=KubernetesClient.from_environment(),
            tracker=PhaseTracker(),
        )
        return runner.run()


_INSTANCE = RunBootstrapJobEntryPoint()
main = _INSTANCE.main


__all__ = [
    "ConfigError",
    "MediaStackError",
    "PhaseTracker",
    "RunBootstrapJobConfig",
    "RunBootstrapJobEntryPoint",
    "RunBootstrapJobRunner",
    "main",
]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MediaStackError as exc:
        err(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        err("Interrupted.")
        raise SystemExit(130)
