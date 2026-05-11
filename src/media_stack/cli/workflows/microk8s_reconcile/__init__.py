"""``cli/workflows/microk8s_reconcile/`` — microk8s reconcile orchestration.

ADR-0015 Phase 7a. The sub-package contains five SRP classes
(each with a named GoF pattern in its docstring) that together
implement the ``bin/k8s/microk8s-reconcile.sh`` workflow:

* :class:`ReconcileConfigLoader` (Repository) — loads + parses
  the ``adapter_hooks.microk8s_reconcile`` bootstrap-config sub-tree
  into the frozen dataclasses below.
* :class:`ManifestApplier` (Strategy) — three apply-manifest phase
  handlers (base kustomize, optional manifests, conditional manifests).
* :class:`RolloutCoordinator` (Strategy) — rollout-restart + wait
  + status-print + failure-escalation phase handlers.
* :class:`Microk8sReconcileService` (Composition Root + Template Method) —
  wires the strategies, owns the dispatch loop + accumulated state.

The commands-tier shim ``cli/commands/microk8s_reconcile_main.py``
provides argparse + exit-code translation only; everything else
goes through this sub-package.
"""

from media_stack.cli.workflows.microk8s_reconcile.config_loader import (
    ReconcileConfigLoader,
)
from media_stack.cli.workflows.microk8s_reconcile.manifest_applier import (
    ManifestApplier,
)
from media_stack.cli.workflows.microk8s_reconcile.models import (
    ConditionalManifestRule,
    Microk8sReconcileConfig,
    Microk8sReconcileState,
    ReconcilePhaseStep,
)
from media_stack.cli.workflows.microk8s_reconcile.reconcile_service import (
    Microk8sReconcileService,
)
from media_stack.cli.workflows.microk8s_reconcile.rollout_coordinator import (
    RolloutCoordinator,
)


__all__ = [
    "ConditionalManifestRule",
    "ManifestApplier",
    "Microk8sReconcileConfig",
    "Microk8sReconcileService",
    "Microk8sReconcileState",
    "ReconcileConfigLoader",
    "ReconcilePhaseStep",
    "RolloutCoordinator",
]
