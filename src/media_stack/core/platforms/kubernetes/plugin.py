"""Kubernetes platform plugin bindings."""

from __future__ import annotations

from media_stack.core.platform_plugin_contract import PlatformPlugin
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient
from media_stack.core.platforms.kubernetes.rebuild_platform_adapter import (
    KubernetesRebuildPlatformAdapter,
    KubernetesRebuildPlatformConfig,
)
from media_stack.core.platforms.kubernetes.services.runner_bindings import (
    build_kubernetes_runner_request,
)


def _build_adapter(request: object, require_dependency) -> object:
    return KubernetesRebuildPlatformAdapter(
        cfg=KubernetesRebuildPlatformConfig(
            namespace=request.environment_id,
            target=request.target,
        ),
        namespace_service=require_dependency(
            request, request.namespace_service, "namespace_service"
        ),
        manifest_apply_service=require_dependency(
            request, request.manifest_apply_service, "manifest_apply_service"
        ),
        ingress_service=require_dependency(request, request.ingress_service, "ingress_service"),
        deployments_wait_service=require_dependency(
            request, request.deployments_wait_service, "deployments_wait_service"
        ),
        smoke_test_service=require_dependency(
            request, request.smoke_test_service, "smoke_test_service"
        ),
        secret_preservation_service=require_dependency(
            request, request.secret_preservation_service, "secret_preservation_service"
        ),
        info=request.info,
        run_kubectl=require_dependency(request, request.run_kubectl, "run_kubectl"),
    )


def _build_runner_request(runner: object, info_fn) -> dict[str, object]:
    return build_kubernetes_runner_request(runner, info_fn)


def _configure_runner(runner: object) -> None:
    runner.kube = KubernetesClient.from_environment()


def _run_bootstrap(runner: object) -> None:
    runner.run_bootstrap_pipeline()


PLUGIN = PlatformPlugin(
    key="k8s",
    aliases=("k8s", "kubernetes", "microk8s"),
    build_adapter=_build_adapter,
    build_runner_request=_build_runner_request,
    configure_runner=_configure_runner,
    run_bootstrap=_run_bootstrap,
    bootstrap_phase_name="Run bootstrap pipeline",
    supports_secret_lifecycle=True,
    supports_secret_generation=True,
    supports_ingress_patch=True,
    supports_scale_policy_guardrails=True,
    supports_failure_status_snapshot=True,
    requires_dynamic_pvc_storage_mode=True,
)
