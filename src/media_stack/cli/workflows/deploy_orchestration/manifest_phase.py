"""DeployManifestPhase — Command class for host prep + manifest apply.

ADR-0015 Phase 4. Pre-Phase-4 these methods lived on
``RunnerPhasesMixin``. The split groups together every phase
that prepares + applies the cluster-side definition of the
stack: host-directory layout, optional namespace teardown,
manifest apply for the active profile, and ingress-class
patching.

Command pattern: each public method is a self-contained phase
action that the orchestrator dispatches through
:meth:`DeployPipelineRunner._run_phase`. Phases that the platform
doesn't support raise :class:`SkipPhase` so the tracker records
them as skipped without inventing a no-op result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.cli.workflows.deploy_errors import DeployError, SkipPhase

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_orchestration.deploy_service_factories import (
        DeployServiceFactoryBundle,
    )
    from media_stack.cli.workflows.deploy_orchestration.platform_adapter_factory import (
        PlatformAdapterFactory,
    )
    from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
        DeployRuntimeOptions,
    )


class DeployManifestPhase:
    """Command set: host prep + namespace + manifest apply + ingress patch."""

    def __init__(
        self,
        services: "DeployServiceFactoryBundle",
        platform_factory: "PlatformAdapterFactory",
        runtime_options: "DeployRuntimeOptions",
        runner: object,
    ) -> None:
        self._services = services
        self._platform_factory = platform_factory
        self._runtime_options = runtime_options
        self._runner = runner

    def prepare_host_directories(self, storage_mode: str) -> None:
        handled = self._services.pipeline_service().prepare_host_directories(storage_mode)
        if not handled:
            raise SkipPhase()

    def delete_namespace_optional(self) -> None:
        delete_flag = "1" if self._runtime_options.delete_environment_enabled() else "0"
        handled = self._platform_factory.adapter(self._runner).delete_environment_optional(
            delete_flag,
        )
        if not handled:
            raise SkipPhase()

    def apply_manifests_for_profile(self) -> None:
        self._platform_factory.adapter(self._runner).apply_environment_definition()

    def pick_ingress_class(self, info_fn) -> str:
        request_payload = self._platform_factory.platform_plugin().build_runner_request(
            self._runner, info_fn,
        )
        ingress_service = request_payload.get("ingress_service")
        if ingress_service is None or not hasattr(ingress_service, "pick_ingress_class"):
            raise DeployError("Ingress class selection is unavailable for this platform target.")
        return str(ingress_service.pick_ingress_class() or "").strip()

    def patch_ingress_class(self) -> None:
        handled = self._platform_factory.adapter(self._runner).reconcile_edge_routing()
        if not handled:
            raise SkipPhase()


__all__ = ["DeployManifestPhase"]
