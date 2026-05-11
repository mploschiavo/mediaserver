"""DeployVerifyPhase — Command class for readiness + smoke + chaos + status.

ADR-0015 Phase 4. Pre-Phase-4 these methods lived on
``RunnerPhasesMixin``. The split groups every phase that
verifies the deploy: wait-for-deployments, ingress smoke test,
chaos recovery tests, final pod-status print, and the at-
failure pod-status snapshot.

Command pattern: each public method is a phase action invoked
via :meth:`DeployPipelineRunner._run_phase`. Some phases write
back into cfg (``run_smoke_test`` resolves a node-IP); the
caller does the assignment so the side-effect is visible at the
call site, not buried in the phase class.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Callable

from media_stack.cli.workflows.deploy_errors import DeployError, SkipPhase
from media_stack.core.cli_common import warn

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )
    from media_stack.cli.workflows.deploy_orchestration.platform_adapter_factory import (
        PlatformAdapterFactory,
    )
    from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
        DeployRuntimeOptions,
    )


class DeployVerifyPhase:
    """Command set: wait + smoke + chaos + final status + failure snapshot."""

    def __init__(
        self,
        cfg: "DeployStackConfig",
        platform_factory: "PlatformAdapterFactory",
        runtime_options: "DeployRuntimeOptions",
        info_fn: Callable[[str], None],
        kube: Any | None,
        runner: object,
    ) -> None:
        self._cfg = cfg
        self._platform_factory = platform_factory
        self._runtime_options = runtime_options
        self._info_fn = info_fn
        self._kube = kube
        self._runner = runner

    def wait_for_deployments(self) -> None:
        try:
            self._platform_factory.adapter(self._runner).wait_for_workloads()
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc

    def run_smoke_test(self) -> str | None:
        """Returns the resolved node-IP (or None if the test was skipped).

        Caller assigns the returned value back onto cfg.node_ip so
        downstream phases (e.g. operator-log banner) see the
        resolved address.
        """
        resolved = self._platform_factory.adapter(self._runner).run_smoke_test()
        if not resolved:
            raise SkipPhase()
        return resolved

    def run_chaos_tests(self) -> None:
        adapter = self._platform_factory.adapter(self._runner)
        chaos_runner = getattr(adapter, "run_chaos_tests", None)
        if not callable(chaos_runner):
            self._info_fn(
                "Chaos testing is enabled but this platform adapter does not implement chaos hooks; "
                "skipping."
            )
            raise SkipPhase()
        chaos_runner(
            duration_minutes=int(self._cfg.chaos_duration_minutes),
            interval_seconds=int(self._cfg.chaos_interval_seconds),
            actions=self._runtime_options.chaos_actions(),
        )

    def print_final_pod_status(self) -> None:
        self._platform_factory.adapter(self._runner).print_workload_status()

    def emit_failure_status_snapshot(self) -> None:
        plugin = self._platform_factory.platform_plugin()
        if not plugin.supports_failure_status_snapshot:
            warn("Platform status snapshot at failure is not configured for this target.")
            return
        if self._kube is None or not hasattr(self._kube, "run"):
            warn("Platform status snapshot at failure is unavailable: kube client not configured.")
            return
        warn("Pod status snapshot at failure:")
        result = self._kube.run(
            ["-n", self._cfg.namespace, "get", "pods", "-o", "wide"], check=False,
        )
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)


__all__ = ["DeployVerifyPhase"]
