"""``cli/workflows/run_controller_job_orchestration/`` — bootstrap-job pipeline.

ADR-0015 Phase 7c. The sub-package contains six SRP classes
(each with a named GoF pattern in its docstring) that together
implement the ``bin/k8s/run-controller-job.sh`` workflow — the
K8s mirror of the deploy-pipeline ``DeployStackRunner`` that
Phase 4 retired:

* :class:`BootstrapJobServiceBundle` (Factory bundle) — builds
  the ten workflow services the pipeline uses.
* :class:`BootstrapJobConfigResolver` (Repository) — resolves +
  caches the bootstrap config + ``adapter_hooks.bootstrap_job``
  sub-tree (post-job actions, call-handler specs, runtime-config-
  policy handler spec).
* :class:`BootstrapHookDispatcher` (Strategy) — imports declared
  hook specs (``module.path:Symbol``) and invokes them with a
  filtered context view.
* :class:`BootstrapPrimingPhase` (Command set) — collapses the
  pre-Phase-7c ``_RunBootstrapJobPrimingMixin`` mixin (75 LoC of
  prime/restart/log/secret-read methods) onto a proper class.
* :class:`BootstrapPhase` (Command set) — prepare-config + PVC
  prereqs + manifest-overrides + bootstrap-service trigger/wait.
* :class:`RunBootstrapJobPipeline` (Composition Root + Template
  Method) — wires the above + owns the dispatch loop + the
  test-surface compatibility shims.
"""

from media_stack.cli.workflows.run_controller_job_orchestration.bootstrap_phase import (
    BootstrapPhase,
)
from media_stack.cli.workflows.run_controller_job_orchestration.config_resolver import (
    BootstrapJobConfigResolver,
)
from media_stack.cli.workflows.run_controller_job_orchestration.hook_dispatcher import (
    BootstrapHookDispatcher,
)
from media_stack.cli.workflows.run_controller_job_orchestration.pipeline import (
    RunBootstrapJobPipeline,
)
from media_stack.cli.workflows.run_controller_job_orchestration.priming_phase import (
    BootstrapPrimingPhase,
)
from media_stack.cli.workflows.run_controller_job_orchestration.service_factory_bundle import (
    BootstrapJobServiceBundle,
)


__all__ = [
    "BootstrapHookDispatcher",
    "BootstrapJobConfigResolver",
    "BootstrapJobServiceBundle",
    "BootstrapPhase",
    "BootstrapPrimingPhase",
    "RunBootstrapJobPipeline",
]
