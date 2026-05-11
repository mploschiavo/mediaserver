"""``cli/workflows/dup_burndown/`` — duplicate-code burn-down workflow.

ADR-0015 Phase 7g. Two SRP classes:

* :class:`DupBurndownDetector` (Repository) — AST function-body
  detection + PMD CPD invocation + baseline file rw.
* :class:`DupBurndownRunner` (Workflow runner) — the three
  subcommand handlers (report / tighten / check) + top-cluster
  printer.
"""

from media_stack.cli.workflows.dup_burndown.detector import DupBurndownDetector
from media_stack.cli.workflows.dup_burndown.runner import DupBurndownRunner


__all__ = ["DupBurndownDetector", "DupBurndownRunner"]
