#!/usr/bin/env python3
"""Python CLI for rebuild-and-bootstrap orchestration.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import ipaddress
import json
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.docker import DockerClient
from core.kube import KubernetesClient
from core.phase_tracker import PhaseTracker
from core.platform_adapter import (
    RebuildPlatformAdapter,
    RebuildPlatformAdapterBuildRequest,
    build_rebuild_platform_adapter,
    normalize_platform_target,
)
from core.subprocess_utils import CommandResult

from cli.bootstrap_notification_service import (
    BootstrapNotificationConfig,
    BootstrapNotificationService,
)
from cli.rebuild_cli_config_service import (
    RebuildBootstrapConfig,
    parse_rebuild_bootstrap_config,
)
from cli.rebuild_deployments_wait_service import (
    RebuildDeploymentsWaitConfig,
    RebuildDeploymentsWaitService,
)
from cli.rebuild_ingress_service import RebuildIngressConfig, RebuildIngressService
from cli.rebuild_manifest_apply_service import (
    RebuildManifestApplyConfig,
    RebuildManifestApplyService,
)
from cli.rebuild_manifest_overrides_service import (
    RebuildManifestOverridesConfig,
    RebuildManifestOverridesService,
)
from cli.rebuild_namespace_service import RebuildNamespaceConfig, RebuildNamespaceService
from cli.rebuild_pipeline_service import RebuildPipelineConfig, RebuildPipelineService
from cli.rebuild_profile_defaults_service import RebuildProfileDefaultsService
from cli.rebuild_script_runner_service import (
    RebuildScriptRunnerConfig,
    RebuildScriptRunnerService,
)
from cli.rebuild_secret_preservation_service import (
    RebuildSecretPreservationConfig,
    RebuildSecretPreservationService,
)
from cli.rebuild_smoke_test_service import RebuildSmokeTestService


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


class RebuildError(RuntimeError):
    """Raised when rebuild/bootstrap orchestration fails."""


class SkipPhase(RuntimeError):
    """Signal that current phase should be marked as skipped."""


@dataclass
class RebuildBootstrapRunner:
    cfg: RebuildBootstrapConfig
    kube: KubernetesClient | None = None
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))
    backup_secret_values: dict[str, str] = field(default_factory=dict)
    _resolved_config_cache: dict[str, object] | None = field(default=None, init=False, repr=False)
    _platform_adapter_cache: RebuildPlatformAdapter | None = field(
        default=None, init=False, repr=False
    )
    _docker_client_cache: DockerClient | None = field(default=None, init=False, repr=False)

    def _resolved_bootstrap_config(self) -> dict[str, object]:
        if self._resolved_config_cache is None:
            payload = json.loads(self.cfg.config_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise RebuildError(
                    f"Expected JSON object in bootstrap config file: {self.cfg.config_file}"
                )
            self._resolved_config_cache = payload
        return self._resolved_config_cache

    def _rebuild_profile_actions(
        self,
    ) -> tuple[
        dict[str, tuple[str, ...]],
        dict[str, tuple[str, ...]],
        dict[str, str],
        dict[str, tuple[str, ...]],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        cfg = self._resolved_bootstrap_config()
        adapter_hooks = cfg.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}, {}, {}, {}, (), ()
        rebuild_hooks = adapter_hooks.get("rebuild")
        if not isinstance(rebuild_hooks, dict):
            return {}, {}, {}, {}, (), ()

        scale_to_zero: dict[str, tuple[str, ...]] = {}
        raw_scale_to_zero = rebuild_hooks.get("profile_scale_to_zero_apps")
        if raw_scale_to_zero is not None:
            if not isinstance(raw_scale_to_zero, dict):
                raise RebuildError(
                    "adapter_hooks.rebuild.profile_scale_to_zero_apps must be an object"
                )
            for profile, apps in raw_scale_to_zero.items():
                profile_key = str(profile or "").strip()
                if not profile_key:
                    continue
                if not isinstance(apps, list):
                    raise RebuildError(
                        "adapter_hooks.rebuild.profile_scale_to_zero_apps."
                        f"{profile_key} must be an array"
                    )
                resolved_apps = tuple(
                    str(app or "").strip() for app in apps if str(app or "").strip()
                )
                scale_to_zero[profile_key] = resolved_apps

        tls_hosts: dict[str, tuple[str, ...]] = {}
        tls_secret_names: dict[str, str] = {}
        raw_tls_profiles = rebuild_hooks.get("profile_tls")
        if raw_tls_profiles is not None:
            if not isinstance(raw_tls_profiles, dict):
                raise RebuildError("adapter_hooks.rebuild.profile_tls must be an object")
            for profile, spec in raw_tls_profiles.items():
                profile_key = str(profile or "").strip()
                if not profile_key:
                    continue
                if not isinstance(spec, dict):
                    raise RebuildError(
                        f"adapter_hooks.rebuild.profile_tls.{profile_key} must be an object"
                    )
                raw_hosts = spec.get("hosts")
                if raw_hosts is not None:
                    if not isinstance(raw_hosts, list):
                        raise RebuildError(
                            f"adapter_hooks.rebuild.profile_tls.{profile_key}.hosts must be an array"
                        )
                    hosts = tuple(
                        str(host or "").strip() for host in raw_hosts if str(host or "").strip()
                    )
                    tls_hosts[profile_key] = hosts
                secret_name = str(spec.get("secret_name") or "").strip()
                if secret_name:
                    tls_secret_names[profile_key] = secret_name

        profile_manifest_paths: dict[str, tuple[str, ...]] = {}
        raw_profile_manifest_paths = rebuild_hooks.get("profile_manifest_paths")
        if raw_profile_manifest_paths is not None:
            if not isinstance(raw_profile_manifest_paths, dict):
                raise RebuildError("adapter_hooks.rebuild.profile_manifest_paths must be an object")
            for profile, manifests in raw_profile_manifest_paths.items():
                profile_key = str(profile or "").strip()
                if not profile_key:
                    continue
                if not isinstance(manifests, list):
                    raise RebuildError(
                        "adapter_hooks.rebuild.profile_manifest_paths."
                        f"{profile_key} must be an array"
                    )
                profile_manifest_paths[profile_key] = tuple(
                    str(item or "").strip() for item in manifests if str(item or "").strip()
                )

        component_enable_manifest_paths: tuple[str, ...] = ()
        raw_component_manifest_paths = rebuild_hooks.get("component_enable_manifest_paths")
        if raw_component_manifest_paths is not None:
            if not isinstance(raw_component_manifest_paths, list):
                raise RebuildError(
                    "adapter_hooks.rebuild.component_enable_manifest_paths must be an array"
                )
            component_enable_manifest_paths = tuple(
                str(item or "").strip()
                for item in raw_component_manifest_paths
                if str(item or "").strip()
            )

        preserve_secret_keys: tuple[str, ...] = ()
        raw_preserve_secret_keys = rebuild_hooks.get("preserve_secret_keys")
        if raw_preserve_secret_keys is not None:
            if not isinstance(raw_preserve_secret_keys, list):
                raise RebuildError("adapter_hooks.rebuild.preserve_secret_keys must be an array")
            preserve_secret_keys = tuple(
                str(item or "").strip()
                for item in raw_preserve_secret_keys
                if str(item or "").strip()
            )

        return (
            scale_to_zero,
            tls_hosts,
            tls_secret_names,
            profile_manifest_paths,
            component_enable_manifest_paths,
            preserve_secret_keys,
        )

    def _notification_service(self) -> BootstrapNotificationService:
        return BootstrapNotificationService(
            cfg=BootstrapNotificationConfig(
                alert_webhook_url=self.cfg.alert_webhook_url,
            )
        )

    def _script_runner_service(self) -> RebuildScriptRunnerService:
        return RebuildScriptRunnerService(
            cfg=RebuildScriptRunnerConfig(
                root_dir=self.cfg.root_dir,
                namespace=self.cfg.namespace,
            )
        )

    def _secret_preservation_service(self) -> RebuildSecretPreservationService:
        _, _, _, _, _, preserve_secret_keys = self._rebuild_profile_actions()
        return RebuildSecretPreservationService(
            cfg=RebuildSecretPreservationConfig(
                namespace=self.cfg.namespace,
                secret_name=self.cfg.secret_name,
                preserve_keys=preserve_secret_keys,
            ),
            info=info,
            run_kube=self._run_kubectl,
        )

    def _namespace_service(self) -> RebuildNamespaceService:
        return RebuildNamespaceService(
            cfg=RebuildNamespaceConfig(namespace=self.cfg.namespace),
            info=info,
            run_kube=self._run_kubectl,
        )

    def _manifest_overrides_service(self) -> RebuildManifestOverridesService:
        return RebuildManifestOverridesService(
            cfg=RebuildManifestOverridesConfig(
                namespace=self.cfg.namespace,
                prepare_host_root=self.cfg.prepare_host_root,
                ingress_domain=self.cfg.ingress_domain,
                pvc_storage_class=self.cfg.pvc_storage_class,
            ),
            run_kubectl=self._run_kubectl,
        )

    def _manifest_apply_service(self) -> RebuildManifestApplyService:
        (
            profile_scale_to_zero_apps,
            profile_tls_hosts,
            profile_tls_secret_names,
            profile_manifest_paths,
            component_enable_manifest_paths,
            _preserve_secret_keys,
        ) = self._rebuild_profile_actions()
        return RebuildManifestApplyService(
            cfg=RebuildManifestApplyConfig(
                root_dir=self.cfg.root_dir,
                namespace=self.cfg.namespace,
                profile=self.cfg.profile,
                include_optional=self.cfg.include_optional,
                enable_components=self.cfg.enable_components,
                profile_scale_to_zero_apps=profile_scale_to_zero_apps,
                profile_tls_hosts=profile_tls_hosts,
                profile_tls_secret_names=profile_tls_secret_names,
                profile_manifest_paths=profile_manifest_paths,
                component_enable_manifest_paths=component_enable_manifest_paths,
            ),
            info=info,
            warn=warn,
            run_kubectl=self._run_kubectl,
            apply_manifest_text_with_overrides=self._apply_manifest_text_with_overrides,
            apply_manifest_file_with_overrides=self._apply_manifest_file_with_overrides,
        )

    def _profile_defaults_service(self) -> RebuildProfileDefaultsService:
        return RebuildProfileDefaultsService()

    def _ingress_service(self) -> RebuildIngressService:
        return RebuildIngressService(
            cfg=RebuildIngressConfig(
                namespace=self.cfg.namespace,
                ingress_class=self.cfg.ingress_class,
                internet_exposed=self.cfg.internet_exposed,
                route_strategy=self.cfg.route_strategy,
                app_gateway_host=self.cfg.app_gateway_host,
                app_path_prefix=self.cfg.app_path_prefix,
                media_server_direct_host=self.cfg.media_server_direct_host,
                auth_provider=self.cfg.auth_provider,
                auth_middleware=self.cfg.auth_middleware,
            ),
            info=info,
            warn=warn,
            run_kube=self._run_kubectl,
        )

    def _deployments_wait_service(self) -> RebuildDeploymentsWaitService:
        return RebuildDeploymentsWaitService(
            cfg=RebuildDeploymentsWaitConfig(
                namespace=self.cfg.namespace,
                wait_timeout=self.cfg.wait_timeout,
            ),
            info=info,
            warn=warn,
            run_kube=self._run_kubectl,
        )

    def _pipeline_service(self) -> RebuildPipelineService:
        return RebuildPipelineService(
            cfg=RebuildPipelineConfig(
                namespace=self.cfg.namespace,
                root_dir=self.cfg.root_dir,
                prepare_host_root=self.cfg.prepare_host_root,
                enable_components=self.cfg.enable_components,
                preconfigure_api_keys=self.cfg.preconfigure_api_keys,
                apply_initial_preferences=self.cfg.apply_initial_preferences,
                auto_download_content=self.cfg.auto_download_content,
                config_file=self.cfg.config_file,
            ),
            info=info,
            run_script=self._run_script,
        )

    def _smoke_test_service(self) -> RebuildSmokeTestService:
        return RebuildSmokeTestService(
            namespace=self.cfg.namespace,
            node_ip=self.cfg.node_ip,
            info=info,
            warn=warn,
            run_script=self._run_script,
        )

    def _platform_adapter(self) -> RebuildPlatformAdapter:
        if self._platform_adapter_cache is None:
            try:
                resolved_target = self._resolved_platform_target()
                request = RebuildPlatformAdapterBuildRequest(
                    target=resolved_target,
                    environment_id=self.cfg.namespace,
                    info=info,
                )
                if resolved_target == "k8s":
                    request = RebuildPlatformAdapterBuildRequest(
                        target=resolved_target,
                        environment_id=self.cfg.namespace,
                        info=info,
                        namespace_service=self._namespace_service(),
                        manifest_apply_service=self._manifest_apply_service(),
                        ingress_service=self._ingress_service(),
                        deployments_wait_service=self._deployments_wait_service(),
                        smoke_test_service=self._smoke_test_service(),
                        run_kubectl=self._run_kubectl,
                    )
                elif resolved_target == "compose":
                    request = RebuildPlatformAdapterBuildRequest(
                        target=resolved_target,
                        environment_id=self.cfg.namespace,
                        info=info,
                        docker_client=self._docker_client(),
                        compose_file=self.cfg.compose_file,
                        compose_env_file=self.cfg.compose_env_file,
                        compose_project_name=self.cfg.compose_project_name,
                        compose_profiles=self._compose_profiles(),
                        selected_apps=self._selected_apps(),
                        internet_exposed=self._is_truthy(self.cfg.internet_exposed),
                        route_strategy=self.cfg.route_strategy,
                        app_gateway_host=self.cfg.app_gateway_host,
                        app_path_prefix=self.cfg.app_path_prefix,
                        media_server_direct_host=self.cfg.media_server_direct_host,
                        auth_provider=self.cfg.auth_provider,
                        auth_middleware=self.cfg.auth_middleware,
                        wait_timeout=self.cfg.wait_timeout,
                        node_ip=self.cfg.node_ip,
                    )
                self._platform_adapter_cache = build_rebuild_platform_adapter(request=request)
            except ValueError as exc:
                raise RebuildError(str(exc)) from exc
        return self._platform_adapter_cache

    def _docker_client(self) -> DockerClient:
        if self._docker_client_cache is None:
            self._docker_client_cache = DockerClient.from_environment()
        return self._docker_client_cache

    def _compose_profiles(self) -> tuple[str, ...]:
        raw = str(self.cfg.compose_profiles or "").strip()
        if not raw:
            return ()
        return tuple(token.strip() for token in raw.split(",") if token.strip())

    def _selected_apps(self) -> tuple[str, ...]:
        raw = str(self.cfg.selected_apps or "").strip()
        if not raw:
            return ()
        return tuple(token.strip() for token in raw.split(",") if token.strip())

    def _is_truthy(self, value: str) -> bool:
        token = str(value or "").strip().lower()
        return token in {"1", "true", "yes", "on", "y"}

    def _resolved_platform_target(self) -> str:
        resolved = normalize_platform_target(self.cfg.platform_target)
        if not resolved:
            raise RebuildError("PLATFORM_TARGET cannot be empty.")
        return resolved

    def run(self) -> int:
        self._validate_inputs()
        target = self._resolved_platform_target()
        is_k8s = target == "k8s"

        info("Starting full media-stack rebuild/bootstrap")
        self._run_phase("Resolve profile defaults", self.apply_profile_defaults)
        info(f"Namespace: {self.cfg.namespace}")
        info(f"Profile: {self.cfg.profile}")
        info(f"Platform target: {target}")
        info(f"Purpose: {self.cfg.purpose}")
        info(f"Disk allocation (GB): {self.cfg.disk_allocation_gb}")
        info(f"Network CIDR: {self.cfg.network_cidr}")
        info(f"Ingress domain: {self.cfg.ingress_domain}")
        info(f"Config: {self.cfg.config_file}")
        info(f"Delete namespace: {self.cfg.delete_namespace}")
        info(f"Storage mode: {self.cfg.storage_mode}")
        if self.cfg.pvc_storage_class:
            info(f"PVC storage class override: {self.cfg.pvc_storage_class}")
        else:
            info("PVC storage class override: <cluster default>")
        info(f"Include optional: {self.cfg.include_optional}")
        info(f"Enable components: {self.cfg.enable_components}")
        info(f"Run bootstrap: {self.cfg.run_bootstrap}")
        info(f"Preconfigure API keys: {self.cfg.preconfigure_api_keys}")
        info(f"Apply initial preferences: {self.cfg.apply_initial_preferences}")
        info(f"Auto-download content: {self.cfg.auto_download_content}")
        info(f"Generate secrets on rebuild: {self.cfg.generate_secrets_on_rebuild}")
        info(f"Preserve secret on rebuild: {self.cfg.preserve_secret_on_rebuild}")
        info(f"Selected apps: {self.cfg.selected_apps or '<all>'}")
        info(
            "Exposure: "
            f"internet={self.cfg.internet_exposed}, "
            f"route_strategy={self.cfg.route_strategy}, "
            f"auth_provider={self.cfg.auth_provider}"
        )
        if self.cfg.app_gateway_host:
            info(f"App gateway host: {self.cfg.app_gateway_host}")
        if self.cfg.media_server_direct_host:
            info(f"Media-server direct host: {self.cfg.media_server_direct_host}")

        self.notify(
            "info",
            f"media-stack rebuild/bootstrap started (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )

        self._run_phase(
            "Validate bootstrap config schema",
            lambda: self._run_script(
                "validate-bootstrap-config.sh", "--config", str(self.cfg.config_file)
            ),
        )

        if self.cfg.skip_prepare_host != "1":
            self._run_phase("Prepare host directories", self.prepare_host_directories)
        else:
            self._run_phase("Prepare host directories", lambda: None, enabled=False)

        self._run_phase(
            "Backup existing credentials",
            self.backup_existing_secret_values,
            enabled=is_k8s,
        )
        self._run_phase("Delete namespace (optional)", self.delete_namespace_optional)
        self._run_phase("Apply manifests for profile", self.apply_manifests_for_profile)

        if is_k8s and self.cfg.generate_secrets_on_rebuild == "1":
            self._run_phase("Generate secrets", self.generate_secrets)
        else:
            self._run_phase("Generate secrets", lambda: None, enabled=False)

        self._run_phase(
            "Restore preserved credentials",
            self.restore_secret_values_from_backup,
            enabled=is_k8s,
        )
        self._run_phase("Patch ingress class", self.patch_ingress_class, enabled=is_k8s)
        self._run_phase("Wait for deployments", self.wait_for_deployments)

        if is_k8s and self.cfg.run_bootstrap == "1":
            self._run_phase("Apply scale-policy guardrails", self.apply_scale_policy_guardrails)
            self._run_phase("Run bootstrap pipeline", self.run_bootstrap_pipeline)
        else:
            self._run_phase(
                "Apply scale-policy guardrails", self.skip_scale_policy_guardrails, enabled=True
            )
            self._run_phase("Run bootstrap pipeline", self.skip_bootstrap_pipeline, enabled=True)

        if self.cfg.run_smoke_test == "1":
            self._run_phase("Run ingress smoke test", self.run_smoke_test)
        else:
            self._run_phase("Run ingress smoke test", lambda: None, enabled=False)

        self._run_phase("Collect final pod status", self.print_final_pod_status)
        self.tracker.summary()

        print("\n[OK] Rebuild + bootstrap completed.")
        self.notify(
            "ok",
            f"media-stack rebuild/bootstrap succeeded (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )
        return 0

    def _run_phase(self, name: str, fn: Callable[[], None], *, enabled: bool = True) -> None:
        self.tracker.start(name)
        if not enabled:
            self.tracker.end("skipped")
            return
        try:
            fn()
            self.tracker.end("ok")
        except SkipPhase:
            self.tracker.end("skipped")
        except Exception:
            self.tracker.end("failed")
            raise

    def _validate_inputs(self) -> None:
        if not self.cfg.config_file.exists():
            raise RebuildError(f"Config file not found: {self.cfg.config_file}")
        if not self.cfg.namespace.strip():
            raise RebuildError("NAMESPACE cannot be empty.")
        self._resolved_platform_target()
        self.cfg.ingress_domain = self.cfg.ingress_domain.lstrip(".").strip()
        if not self.cfg.ingress_domain:
            raise RebuildError("INGRESS_DOMAIN cannot be empty.")
        if self._resolved_platform_target() == "k8s" and self.cfg.storage_mode != "dynamic-pvc":
            raise RebuildError(
                f"Unsupported STORAGE_MODE '{self.cfg.storage_mode}'. "
                "legacy-hostpath was removed; use dynamic-pvc."
            )
        if self.cfg.profile not in {"minimal", "full", "public-demo", "power-user"}:
            raise RebuildError(
                f"Unknown PROFILE '{self.cfg.profile}'. Supported: minimal, full, public-demo, power-user."
            )
        if self.cfg.route_strategy not in {"subdomain", "path-prefix", "hybrid"}:
            raise RebuildError("ROUTE_STRATEGY must be one of: subdomain, path-prefix, hybrid.")
        if self.cfg.auth_provider not in {"none", "authelia", "authentik"}:
            raise RebuildError("AUTH_PROVIDER must be one of: none, authelia, authentik.")
        if self.cfg.disk_allocation_gb < 200:
            raise RebuildError("STACK_DISK_ALLOCATION_GB must be at least 200.")
        try:
            network = ipaddress.ip_network(self.cfg.network_cidr, strict=False)
        except ValueError as exc:
            raise RebuildError(f"Invalid STACK_NETWORK_CIDR '{self.cfg.network_cidr}'.") from exc
        if not network.is_private:
            raise RebuildError("STACK_NETWORK_CIDR must be private (10/8, 172.16/12, 192.168/16).")

    def apply_profile_defaults(self) -> None:
        try:
            resolved = self._profile_defaults_service().apply(
                profile=self.cfg.profile,
                include_optional=self.cfg.include_optional,
                enable_components=self.cfg.enable_components,
                run_bootstrap=self.cfg.run_bootstrap,
            )
        except RuntimeError as exc:
            raise RebuildError(str(exc)) from exc
        self.cfg.include_optional = resolved.include_optional
        self.cfg.enable_components = resolved.enable_components
        self.cfg.run_bootstrap = resolved.run_bootstrap
        if self._resolved_platform_target() != "k8s" and self.cfg.run_bootstrap == "1":
            info("Non-k8s platform target selected; forcing RUN_BOOTSTRAP=0.")
            self.cfg.run_bootstrap = "0"

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        try:
            self._script_runner_service().run_script(script_name, *args, env=env)
        except RuntimeError as exc:
            raise RebuildError(str(exc)) from exc

    def _run_kubectl(
        self,
        args: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        if self.kube is None:
            raise RebuildError("Kubernetes client not configured for this platform target.")
        proc = self.kube.run(
            args,
            check=False,
            input_text=input_text,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if check and proc.returncode != 0:
            raise RebuildError(
                f"Kubernetes command failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in proc.args)}"
            )
        return proc

    def notify(self, status: str, message: str) -> None:
        self._notification_service().notify(status, message)

    def prepare_host_directories(self) -> None:
        handled = self._pipeline_service().prepare_host_directories(self.cfg.storage_mode)
        if not handled:
            raise SkipPhase()

    def backup_existing_secret_values(self) -> None:
        self.backup_secret_values = self._secret_preservation_service().backup_existing_values(
            self.cfg.preserve_secret_on_rebuild,
        )

    def restore_secret_values_from_backup(self) -> None:
        self._secret_preservation_service().restore_values(self.backup_secret_values)

    def delete_namespace_optional(self) -> None:
        handled = self._platform_adapter().delete_environment_optional(self.cfg.delete_namespace)
        if not handled:
            raise SkipPhase()

    def _apply_manifest_text_with_overrides(self, text: str) -> None:
        self._manifest_overrides_service().apply_manifest_text_with_overrides(text)

    def _apply_manifest_file_with_overrides(self, file_path: Path) -> None:
        self._manifest_overrides_service().apply_manifest_file_with_overrides(file_path)

    def apply_manifests_for_profile(self) -> None:
        self._platform_adapter().apply_environment_definition()

    def generate_secrets(self) -> None:
        self._pipeline_service().generate_secrets()

    def pick_ingress_class(self) -> str:
        return self._ingress_service().pick_ingress_class()

    def patch_ingress_class(self) -> None:
        handled = self._platform_adapter().reconcile_edge_routing()
        if not handled:
            raise SkipPhase()

    def wait_for_deployments(self) -> None:
        try:
            self._platform_adapter().wait_for_workloads()
        except RuntimeError as exc:
            raise RebuildError(str(exc)) from exc

    def apply_scale_policy_guardrails(self) -> None:
        self._pipeline_service().apply_scale_policy_guardrails()

    def skip_scale_policy_guardrails(self) -> None:
        info("Scale-policy guardrails skipped for non-bootstrap profile.")
        raise SkipPhase()

    def run_bootstrap_pipeline(self) -> None:
        self._pipeline_service().run_bootstrap_pipeline()

    def skip_bootstrap_pipeline(self) -> None:
        info("Bootstrap skipped by profile/policy.")
        raise SkipPhase()

    def run_smoke_test(self) -> None:
        resolved = self._platform_adapter().run_smoke_test()
        self.cfg.node_ip = resolved or self.cfg.node_ip
        if not resolved:
            raise SkipPhase()

    def print_final_pod_status(self) -> None:
        self._platform_adapter().print_workload_status()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root_dir = Path(__file__).resolve().parents[2]
    cfg = parse_rebuild_bootstrap_config(args, root_dir=root_dir)
    target = normalize_platform_target(cfg.platform_target)

    kube: KubernetesClient | None = None
    if target == "k8s":
        try:
            kube = KubernetesClient.from_environment()
        except Exception as exc:
            err(str(exc))
            return 2

    runner = RebuildBootstrapRunner(cfg=cfg, kube=kube)
    try:
        return runner.run()
    except Exception as exc:
        warn(f"Rebuild/bootstrap failed: {exc}")
        if target == "k8s" and kube is not None:
            warn("Pod status snapshot at failure:")
            result = kube.run(["-n", cfg.namespace, "get", "pods", "-o", "wide"], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
        else:
            warn("Platform status snapshot at failure is not configured for this target.")
        runner.tracker.summary()
        runner.notify(
            "error",
            f"media-stack rebuild/bootstrap failed (profile={cfg.profile}, namespace={cfg.namespace})",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
