"""``cli/workflows/deploy_orchestration/`` — deploy pipeline orchestration.

ADR-0015 Phase 4. The sub-package contains nine single-responsibility
classes (each with a named GoF pattern in its docstring) that
together implement the deploy/bootstrap pipeline:

* :class:`DeployRuntimeOptions` (Strategy) — cfg-derived runtime
  decisions (compose profiles, selected apps, chaos actions,
  delete-env safeguard, truthy parsing, resolved platform target).
* :class:`RuntimeArtifactWriter` (Repository) — per-run artifact
  directory + text/JSON writes under
  ``<root>/.state/runtime-artifacts/<run-id>/``.
* :class:`K8sManifestCapturer` (Recorder) — captures stdin-piped
  ``kubectl apply -f -`` payloads as audit-trail artifacts.
* :class:`PlatformAdapterFactory` (Factory) — resolves +
  caches platform plugin + adapter + per-key client instances.
* :class:`DeployServiceFactoryBundle` (Factory bundle) — builds
  the four workflow services the pipeline uses
  (notification, script runner, profile defaults, pipeline).
* :class:`DeployPhaseValidator` (Validator) — validates cfg +
  bootstrap config inputs at the top of the pipeline.
* :class:`DeployManifestPhase` (Command set) — host prep +
  namespace delete + manifest apply + ingress patch.
* :class:`DeployBootstrapPhase` (Command set) — secret backup +
  restore + generation + scale-policy + bootstrap-pipeline run.
* :class:`DeployVerifyPhase` (Command set) — wait + smoke +
  chaos + final status + failure snapshot.
* :class:`DeployPipelineRunner` (Composition Root + Template
  Method) — wires the dependency graph + owns the deploy
  template + the test-surface compatibility shims.

Public surface:

* :class:`DeployPipelineRunner` is the entry point — instantiate
  with a :class:`DeployStackConfig` and call ``run()``. The
  commands-tier ``DeployStackRunner`` subclasses this for test-
  patch surface compatibility.
* Individual classes are exposed for direct testing and for
  future call sites that only need one collaborator.
"""

from media_stack.cli.workflows.deploy_orchestration.banner_logger import (
    DeployBannerLogger,
)
from media_stack.cli.workflows.deploy_orchestration.bootstrap_phase import (
    DeployBootstrapPhase,
)
from media_stack.cli.workflows.deploy_orchestration.deploy_pipeline import (
    DeployPipelineRunner,
)
from media_stack.cli.workflows.deploy_orchestration.deploy_service_factories import (
    DeployServiceFactoryBundle,
)
from media_stack.cli.workflows.deploy_orchestration.k8s_manifest_capturer import (
    K8sManifestCapturer,
)
from media_stack.cli.workflows.deploy_orchestration.manifest_phase import (
    DeployManifestPhase,
)
from media_stack.cli.workflows.deploy_orchestration.phase_validator import (
    DeployPhaseValidator,
)
from media_stack.cli.workflows.deploy_orchestration.platform_adapter_factory import (
    PlatformAdapterFactory,
)
from media_stack.cli.workflows.deploy_orchestration.runtime_artifact_writer import (
    RuntimeArtifactWriter,
)
from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
    DeployRuntimeOptions,
)
from media_stack.cli.workflows.deploy_orchestration.verify_phase import (
    DeployVerifyPhase,
)


__all__ = [
    "DeployBannerLogger",
    "DeployBootstrapPhase",
    "DeployManifestPhase",
    "DeployPhaseValidator",
    "DeployPipelineRunner",
    "DeployRuntimeOptions",
    "DeployServiceFactoryBundle",
    "DeployVerifyPhase",
    "K8sManifestCapturer",
    "PlatformAdapterFactory",
    "RuntimeArtifactWriter",
]
