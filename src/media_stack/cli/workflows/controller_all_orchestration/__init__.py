"""``cli/workflows/controller_all_orchestration/`` — bootstrap_all pipeline.

ADR-0015 Phase 7d. The sub-package contains four SRP classes
(each with a named GoF pattern in its docstring) that — together
with the shared :mod:`cli.workflows.controller_phase_planning`
helpers — implement the ``bin/bootstrap-all.sh`` workflow:

* :class:`ControllerAllConfig` (frozen dataclass) — operator-
  tunable config view.
* :class:`ComponentDeployer` (Strategy) — manifest apply +
  scale + rollout-status for a single component.
* :class:`ControllerAllStepExecutors` (Command set) — four
  phase-plan action handlers (component_script / script /
  enable_components / http_action).
* :class:`ControllerAllPipeline` (Composition Root + Template
  Method) — wires shared planning helpers + the two classes
  above + owns the dispatch loop + the resume-checkpoint store.

The commands-tier shim ``cli/commands/controller_all_main.py``
provides argparse + exit-code translation only.
"""

from media_stack.cli.workflows.controller_all_orchestration.component_deployer import (
    ComponentDeployer,
)
from media_stack.cli.workflows.controller_all_orchestration.models import (
    ControllerAllConfig,
)
from media_stack.cli.workflows.controller_all_orchestration.pipeline import (
    ControllerAllPipeline,
)
from media_stack.cli.workflows.controller_all_orchestration.step_executors import (
    ControllerAllStepExecutors,
)


__all__ = [
    "ComponentDeployer",
    "ControllerAllConfig",
    "ControllerAllPipeline",
    "ControllerAllStepExecutors",
]
