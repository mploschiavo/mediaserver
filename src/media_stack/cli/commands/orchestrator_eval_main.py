#!/usr/bin/env python3
"""Entry-point shim for ``bin/ops/orchestrator-eval.sh``.

ADR-0015 Phase 7h. Pre-Phase-7h this module held the full
``OrchestratorEvalCommand`` (227 LoC, 8 methods) — already
SRP-clean and well-tested, but workflow material per the ADR
boundary. Phase 7h moved the class to
:mod:`media_stack.cli.workflows.orchestrator_eval_runner` as
``OrchestratorEvalRunner``. The legacy ``OrchestratorEvalCommand``
name is preserved here as an alias so the 12 existing unit tests
in ``tests/unit/cli/test_orchestrator_eval_main.py`` keep working
without churn.

Usage (unchanged):

    bin/ops/orchestrator-eval                       # default: dry_run, compose
    bin/ops/orchestrator-eval --platform k8s        # eval against k8s registry
    bin/ops/orchestrator-eval --apply               # actually run ensurers
    bin/ops/orchestrator-eval --json                # machine-readable output
"""

from __future__ import annotations

from media_stack.cli.workflows.orchestrator_eval_runner import (
    OrchestratorEvalRunner,
    PromiseOrchestrator,
)


# Back-compat alias: the existing tests import OrchestratorEvalCommand
# by name from this module. Aliasing the workflows-tier class keeps
# the test surface stable across the Phase 7h relocation.
OrchestratorEvalCommand = OrchestratorEvalRunner

_INSTANCE = OrchestratorEvalRunner()
main = _INSTANCE.main


__all__ = [
    "OrchestratorEvalCommand",
    "OrchestratorEvalRunner",
    "PromiseOrchestrator",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
