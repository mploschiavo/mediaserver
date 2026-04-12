"""Kubernetes runner binding helpers for rebuild platform plugin wiring."""

from __future__ import annotations

from typing import Any, Callable

from media_stack.core.platforms.kubernetes.services.rebuild_deployments_wait_service import (
    RebuildDeploymentsWaitConfig,
    RebuildDeploymentsWaitService,
)
from media_stack.core.platforms.kubernetes.services.rebuild_ingress_service import (
    RebuildIngressConfig,
    RebuildIngressService,
)
from media_stack.core.platforms.kubernetes.services.rebuild_manifest_apply_service import (
    RebuildManifestApplyConfig,
    RebuildManifestApplyService,
)
from media_stack.core.platforms.kubernetes.services.rebuild_manifest_overrides_service import (
    RebuildManifestOverridesConfig,
    RebuildManifestOverridesService,
)
from media_stack.core.platforms.kubernetes.services.rebuild_namespace_service import (
    RebuildNamespaceConfig,
    RebuildNamespaceService,
)
from media_stack.core.platforms.kubernetes.services.rebuild_secret_preservation_service import (
    RebuildSecretPreservationConfig,
    RebuildSecretPreservationService,
)
from media_stack.core.platforms.kubernetes.services.rebuild_smoke_test_service import (
    RebuildSmokeTestService,
)

InfoFn = Callable[[str], None]



class KubernetesRunnerBindingsService:
    def build_kubernetes_runner_request(self, runner: Any, info_fn: InfoFn) -> dict[str, object]:
        (
            profile_scale_to_zero_apps,
            profile_tls_hosts,
            profile_tls_secret_names,
            profile_manifest_paths,
            component_enable_manifest_paths,
            preserve_secret_keys,
            base_manifest_paths,
        ) = runner._rebuild_profile_actions()
    
        warn_fn = runner.tracker.warn
        run_kubectl = runner._run_kubectl
    
        manifest_overrides_service = RebuildManifestOverridesService(
            cfg=RebuildManifestOverridesConfig(
                namespace=runner.cfg.namespace,
                prepare_host_root=runner.cfg.prepare_host_root,
                ingress_domain=runner.cfg.ingress_domain,
                pvc_storage_class=runner.cfg.pvc_storage_class,
            ),
            run_kubectl=run_kubectl,
        )
    
        return {
            "target": runner._resolved_platform_target(),
            "environment_id": runner.cfg.namespace,
            "info": info_fn,
            "namespace_service": RebuildNamespaceService(
                cfg=RebuildNamespaceConfig(namespace=runner.cfg.namespace),
                info=info_fn,
                run_kube=run_kubectl,
            ),
            "manifest_apply_service": RebuildManifestApplyService(
                cfg=RebuildManifestApplyConfig(
                    root_dir=runner.cfg.root_dir,
                    namespace=runner.cfg.namespace,
                    profile=runner.cfg.profile,
                    include_optional=runner.cfg.include_optional,
                    enable_components=runner.cfg.enable_components,
                    kustomize_cmd=tuple((*runner.kube.cmd_prefix, "kustomize")),
                    profile_scale_to_zero_apps=profile_scale_to_zero_apps,
                    profile_tls_hosts=profile_tls_hosts,
                    profile_tls_secret_names=profile_tls_secret_names,
                    profile_manifest_paths=profile_manifest_paths,
                    component_enable_manifest_paths=component_enable_manifest_paths,
                    base_manifest_paths=base_manifest_paths,
                ),
                info=info_fn,
                warn=warn_fn,
                run_kubectl=run_kubectl,
                apply_manifest_text_with_overrides=(
                    manifest_overrides_service.apply_manifest_text_with_overrides
                ),
                apply_manifest_file_with_overrides=(
                    manifest_overrides_service.apply_manifest_file_with_overrides
                ),
            ),
            "ingress_service": RebuildIngressService(
                cfg=RebuildIngressConfig(
                    namespace=runner.cfg.namespace,
                    ingress_class=runner.cfg.ingress_class,
                    ingress_class_priority=runner._ingress_class_priority(),
                    internet_exposed=runner.cfg.internet_exposed,
                    route_strategy=runner.cfg.route_strategy,
                    app_gateway_host=runner.cfg.app_gateway_host,
                    app_path_prefix=runner.cfg.app_path_prefix,
                    media_server_direct_host=runner.cfg.media_server_direct_host,
                    auth_provider=runner.cfg.auth_provider,
                    auth_middleware=runner.cfg.auth_middleware,
                ),
                info=info_fn,
                warn=warn_fn,
                run_kube=run_kubectl,
            ),
            "deployments_wait_service": RebuildDeploymentsWaitService(
                cfg=RebuildDeploymentsWaitConfig(
                    namespace=runner.cfg.namespace,
                    wait_timeout=runner.cfg.wait_timeout,
                ),
                info=info_fn,
                warn=warn_fn,
                run_kube=run_kubectl,
            ),
            "smoke_test_service": RebuildSmokeTestService(
                namespace=runner.cfg.namespace,
                node_ip=runner.cfg.node_ip,
                info=info_fn,
                warn=warn_fn,
                run_script=runner._run_script,
            ),
            "secret_preservation_service": RebuildSecretPreservationService(
                cfg=RebuildSecretPreservationConfig(
                    namespace=runner.cfg.namespace,
                    secret_name=runner.cfg.secret_name,
                    preserve_keys=preserve_secret_keys,
                ),
                info=info_fn,
                run_kube=run_kubectl,
            ),
            "run_kubectl": run_kubectl,
        }


_instance = KubernetesRunnerBindingsService()
build_kubernetes_runner_request = _instance.build_kubernetes_runner_request
