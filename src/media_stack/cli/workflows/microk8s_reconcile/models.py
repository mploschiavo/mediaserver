"""Frozen dataclasses for the microk8s-reconcile workflow.

ADR-0015 Phase 7a. Pre-Phase-7a these dataclasses lived next to
``Microk8sReconcileRunner`` in ``cli/commands/microk8s_reconcile_main.py``;
they belong in the workflows tier alongside the service that
consumes them. The mutable :class:`Microk8sReconcileState`
tracks per-run accumulation (rollout failures, optional-deployments-
present); the three frozen dataclasses describe immutable
contract shapes the operator wires through the bootstrap config.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_stack.services.enums import RunnerEvent


@dataclass(frozen=True)
class ConditionalManifestRule:
    deployment: str
    manifest_path: Path
    message: str = ""


@dataclass(frozen=True)
class ReconcilePhaseStep:
    phase_name: str
    event: RunnerEvent
    handler: str
    enabled: bool = True
    when: Any = True


@dataclass(frozen=True)
class Microk8sReconcileConfig:
    namespace: str
    wait_timeout: str
    include_optional: bool
    root_dir: Path
    optional_deployments: tuple[str, ...]
    optional_manifest_paths: tuple[Path, ...]
    conditional_manifest_rules: tuple[ConditionalManifestRule, ...]
    phase_plan: tuple[ReconcilePhaseStep, ...]


@dataclass
class Microk8sReconcileState:
    """Mutable per-run state — accumulated across phase-handler calls."""

    optional_deployments_present: bool = False
    rollout_failures: int = 0


__all__ = [
    "ConditionalManifestRule",
    "Microk8sReconcileConfig",
    "Microk8sReconcileState",
    "ReconcilePhaseStep",
]
