"""Orchestration phases and validation for DeployStackRunner.

Extracted from deploy_stack_main.py — the run() method, _run_phase,
_validate_inputs, and all individual phase action methods.
"""

from __future__ import annotations

import ipaddress
import shlex
import sys
from typing import TYPE_CHECKING, Any, Callable

from media_stack.cli.commands.deploy_stack_errors import (
    DeployError,
    SkipPhase,
    _MIN_STACK_DISK_ALLOCATION_GB,
)
from media_stack.core.edge.provider_registry import router_service_names_by_provider
from media_stack.core.subprocess_utils import CommandResult

from media_stack.cli.workflows.cli_common import info, warn

if TYPE_CHECKING:
    from pathlib import Path

    from media_stack.cli.workflows.deploy_cli_config_service import DeployStackConfig
    from media_stack.core.phase_tracker import PhaseTracker


class RunnerPhasesMixin:
    """Orchestration phases and input validation.

    Requires on the concrete class:
    - ``self.cfg: DeployStackConfig``
    - ``self.kube: Any | None``
    - ``self.tracker: PhaseTracker``
    - ``self.backup_secret_values: dict[str, str]``
    - ``self.info_fn: Callable[[str], None]``
    - All mixin methods from ConfigResolutionMixin and RunnerServicesMixin
    """

    cfg: DeployStackConfig
    kube: Any | None
    tracker: PhaseTracker
    backup_secret_values: dict[str, str]
    info_fn: Callable[[str], None]

    # -- orchestration -----------------------------------------------------

    def run(self) -> int:
        self._validate_inputs()
        self._initialize_runtime_artifacts()
        self._configure_platform_runtime()
        target = self._resolved_platform_target()
        platform_plugin = self._platform_plugin()

        info("Starting full media-stack deploy/bootstrap")
        self._run_phase("Resolve profile defaults", self.apply_profile_defaults)
        info(f"Namespace: {self.cfg.namespace}")
        info(f"Profile: {self.cfg.profile}")
        info(f"Platform target: {target}")
        info(f"Purpose: {self.cfg.purpose}")
        info(f"Disk allocation (GB): {self.cfg.disk_allocation_gb}")
        info(f"Network CIDR: {self.cfg.network_cidr}")
        info(f"Ingress domain: {self.cfg.ingress_domain}")
        info(f"Config: {self.cfg.config_file}")
        delete_requested = self._delete_environment_requested()
        delete_enabled = self._delete_environment_enabled()
        if delete_enabled:
            warn(
                "Delete namespace: ENABLED — existing environment will be fully torn down "
                "(DELETE_NAMESPACE=1 + DELETE_NAMESPACE_CONFIRM). Set DELETE_NAMESPACE=0 to skip teardown."
            )
        elif delete_requested:
            warn("Delete namespace: requested but blocked by safety confirmation safeguard.")
        else:
            info("Delete namespace: disabled (set DELETE_NAMESPACE=1 to enable full teardown)")
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
        if self.cfg.app_gateway_port:
            info(f"App gateway port: {self.cfg.app_gateway_port}")
        if self.cfg.media_server_direct_host:
            info(f"Media-server direct host: {self.cfg.media_server_direct_host}")
        if platform_plugin.logs_bootstrap_runner_image:
            info(f"Compose controller image: {self.cfg.bootstrap_runner_image}")
        info(
            "Chaos testing: "
            f"enabled={self.cfg.chaos_enabled}, "
            f"duration_minutes={self.cfg.chaos_duration_minutes}, "
            f"interval_seconds={self.cfg.chaos_interval_seconds}, "
            f"actions={','.join(self._chaos_actions()) or '<none>'}"
        )

        self.notify(
            "info",
            f"media-stack deploy/bootstrap started (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
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
            enabled=platform_plugin.supports_secret_lifecycle,
        )
        self._run_phase("Delete namespace (optional)", self.delete_namespace_optional)
        self._run_phase("Apply manifests for profile", self.apply_manifests_for_profile)

        if (
            platform_plugin.supports_secret_generation
            and self.cfg.generate_secrets_on_rebuild == "1"
        ):
            self._run_phase("Generate secrets", self.generate_secrets)
        else:
            self._run_phase("Generate secrets", lambda: None, enabled=False)

        self._run_phase(
            "Restore preserved credentials",
            self.restore_secret_values_from_backup,
            enabled=platform_plugin.supports_secret_lifecycle,
        )
        self._run_phase(
            "Patch ingress class",
            self.patch_ingress_class,
            enabled=platform_plugin.supports_ingress_patch,
        )
        self._run_phase("Wait for deployments", self.wait_for_deployments)

        if self.cfg.run_bootstrap == "1":
            if platform_plugin.supports_scale_policy_guardrails:
                self._run_phase("Apply scale-policy guardrails", self.apply_scale_policy_guardrails)
            else:
                self._run_phase(
                    "Apply scale-policy guardrails", self.skip_scale_policy_guardrails, enabled=True
                )
            self._run_phase(platform_plugin.bootstrap_phase_name, self.run_platform_bootstrap)
        else:
            self._run_phase(
                "Apply scale-policy guardrails", self.skip_scale_policy_guardrails, enabled=True
            )
            self._run_phase(platform_plugin.bootstrap_phase_name, self.skip_bootstrap_pipeline)

        if self.cfg.run_smoke_test == "1":
            self._run_phase("Run ingress smoke test", self.run_smoke_test)
        else:
            self._run_phase("Run ingress smoke test", lambda: None, enabled=False)

        if self._is_truthy(self.cfg.chaos_enabled):
            self._run_phase("Run chaos recovery tests", self.run_chaos_tests)
        else:
            self._run_phase("Run chaos recovery tests", lambda: None, enabled=False)

        self._run_phase("Collect final pod status", self.print_final_pod_status)
        self.tracker.summary()

        print("\n[OK] Rebuild + bootstrap completed.")
        self.notify(
            "ok",
            f"media-stack deploy/bootstrap succeeded (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
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

    # -- input validation --------------------------------------------------

    def _validate_inputs(self) -> None:
        if not self.cfg.config_file.exists():
            raise DeployError(f"Config file not found: {self.cfg.config_file}")
        if not self.cfg.namespace.strip():
            raise DeployError("NAMESPACE cannot be empty.")
        platform_plugin = self._platform_plugin()
        self.cfg.ingress_domain = self.cfg.ingress_domain.lstrip(".").strip()
        if not self.cfg.ingress_domain:
            raise DeployError("INGRESS_DOMAIN cannot be empty.")
        if (
            platform_plugin.requires_dynamic_pvc_storage_mode
            and self.cfg.storage_mode != "dynamic-pvc"
        ):
            raise DeployError(
                f"Unsupported STORAGE_MODE '{self.cfg.storage_mode}'. "
                "legacy-hostpath was removed; use dynamic-pvc."
            )
        if self.cfg.profile not in {"minimal", "standard", "full", "public-demo", "power-user"}:
            raise DeployError(
                "Unknown PROFILE "
                f"'{self.cfg.profile}'. Supported: minimal, standard, full, public-demo, power-user."
            )
        valid_route_strategies = set(self._valid_route_strategies())
        if self.cfg.route_strategy not in valid_route_strategies:
            allowed = ", ".join(sorted(valid_route_strategies))
            raise DeployError(f"ROUTE_STRATEGY must be one of: {allowed}.")
        valid_auth_providers = set(self._valid_auth_providers())
        if self.cfg.auth_provider not in valid_auth_providers:
            allowed = ", ".join(sorted(valid_auth_providers))
            raise DeployError(f"AUTH_PROVIDER must be one of: {allowed}.")
        edge_router_provider = self._edge_router_provider()
        valid_edge_router_providers = set(self._valid_edge_router_providers())
        if edge_router_provider and edge_router_provider not in valid_edge_router_providers:
            allowed = ", ".join(sorted(valid_edge_router_providers))
            raise DeployError(
                "EDGE_ROUTER_PROVIDER (or adapter_hooks.edge.router_provider) "
                f"must be one of: {allowed}."
            )
        if (
            self._resolved_platform_target() == "compose"
            and edge_router_provider
            and edge_router_provider != "none"
        ):
            provider_spec = dict(
                self._edge_compose_provider_specs().get(edge_router_provider) or {}
            )
            builtin_provider_keys = set(router_service_names_by_provider().keys())
            if not provider_spec and edge_router_provider not in builtin_provider_keys:
                raise DeployError(
                    "Compose edge provider bindings are missing for "
                    f"'{edge_router_provider}'. "
                    "Define adapter_hooks.edge.compose_provider_specs.<provider> or "
                    "install a provider module under src/media_stack/core/edge/providers/<provider>/."
                )
        if platform_plugin.requires_runtime_config_policy_handler and self.cfg.run_bootstrap == "1":
            if not self._runtime_config_policy_handler_spec():
                raise DeployError(
                    "Compose bootstrap requires "
                    "adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                    "in bootstrap config."
                )
        if not str(self.cfg.bootstrap_runner_image or "").strip():
            raise DeployError("BOOTSTRAP_RUNNER_IMAGE cannot be empty.")
        if self.cfg.disk_allocation_gb < _MIN_STACK_DISK_ALLOCATION_GB:
            raise DeployError(
                "STACK_DISK_ALLOCATION_GB must be at least " f"{_MIN_STACK_DISK_ALLOCATION_GB}."
            )
        if self.cfg.chaos_duration_minutes < 1 or self.cfg.chaos_duration_minutes > 120:
            raise DeployError("CHAOS_DURATION_MINUTES must be between 1 and 120.")
        if self.cfg.chaos_interval_seconds < 0 or self.cfg.chaos_interval_seconds > 3600:
            raise DeployError("CHAOS_INTERVAL_SECONDS must be between 0 and 3600.")
        if self._is_truthy(self.cfg.chaos_enabled) and not self._chaos_actions():
            raise DeployError(
                "CHAOS_ACTIONS must include at least one action when chaos is enabled."
            )
        try:
            network = ipaddress.ip_network(self.cfg.network_cidr, strict=False)
        except ValueError as exc:
            raise DeployError(f"Invalid STACK_NETWORK_CIDR '{self.cfg.network_cidr}'.") from exc
        if not network.is_private:
            raise DeployError("STACK_NETWORK_CIDR must be private (10/8, 172.16/12, 192.168/16).")

    # -- individual phase actions ------------------------------------------

    def apply_profile_defaults(self) -> None:
        try:
            resolved = self._profile_defaults_service().apply(
                profile=self.cfg.profile,
                include_optional=self.cfg.include_optional,
                enable_components=self.cfg.enable_components,
                run_bootstrap=self.cfg.run_bootstrap,
            )
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc
        self.cfg.include_optional = resolved.include_optional
        self.cfg.enable_components = resolved.enable_components
        self.cfg.run_bootstrap = resolved.run_bootstrap

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        try:
            self._script_runner_service().run_script(script_name, *args, env=env)
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc

    def _run_kubectl(
        self,
        args: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        if self.kube is None:
            raise DeployError("Kubernetes client not configured for this platform target.")
        if input_text is not None and self._is_k8s_apply_with_stdin(args):
            self._record_k8s_applied_manifest(args=args, manifest_text=input_text)
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
            raise DeployError(
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
        self.backup_secret_values = self._platform_adapter().backup_secret_values(
            self.cfg.preserve_secret_on_rebuild,
        )

    def restore_secret_values_from_backup(self) -> None:
        self._platform_adapter().restore_secret_values(self.backup_secret_values)

    def delete_namespace_optional(self) -> None:
        delete_flag = "1" if self._delete_environment_enabled() else "0"
        handled = self._platform_adapter().delete_environment_optional(delete_flag)
        if not handled:
            raise SkipPhase()

    def apply_manifests_for_profile(self) -> None:
        self._platform_adapter().apply_environment_definition()

    def generate_secrets(self) -> None:
        self._pipeline_service().generate_secrets()

    def pick_ingress_class(self) -> str:
        request_payload = self._platform_plugin().build_runner_request(self, self.info_fn)
        ingress_service = request_payload.get("ingress_service")
        if ingress_service is None or not hasattr(ingress_service, "pick_ingress_class"):
            raise DeployError("Ingress class selection is unavailable for this platform target.")
        return str(ingress_service.pick_ingress_class() or "").strip()

    def patch_ingress_class(self) -> None:
        handled = self._platform_adapter().reconcile_edge_routing()
        if not handled:
            raise SkipPhase()

    def wait_for_deployments(self) -> None:
        try:
            self._platform_adapter().wait_for_workloads()
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc

    def apply_scale_policy_guardrails(self) -> None:
        self._pipeline_service().apply_scale_policy_guardrails()

    def skip_scale_policy_guardrails(self) -> None:
        info("Scale-policy guardrails skipped for non-bootstrap profile.")
        raise SkipPhase()

    def run_bootstrap_pipeline(self) -> None:
        self._pipeline_service().run_bootstrap_pipeline()

    def run_platform_bootstrap(self) -> None:
        try:
            self._platform_plugin().run_bootstrap(self)
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc

    def skip_bootstrap_pipeline(self) -> None:
        info("Bootstrap skipped by profile/policy.")
        raise SkipPhase()

    def run_smoke_test(self) -> None:
        resolved = self._platform_adapter().run_smoke_test()
        self.cfg.node_ip = resolved or self.cfg.node_ip
        if not resolved:
            raise SkipPhase()

    def run_chaos_tests(self) -> None:
        adapter = self._platform_adapter()
        runner = getattr(adapter, "run_chaos_tests", None)
        if not callable(runner):
            info(
                "Chaos testing is enabled but this platform adapter does not implement chaos hooks; "
                "skipping."
            )
            raise SkipPhase()
        runner(
            duration_minutes=int(self.cfg.chaos_duration_minutes),
            interval_seconds=int(self.cfg.chaos_interval_seconds),
            actions=self._chaos_actions(),
        )

    def print_final_pod_status(self) -> None:
        self._platform_adapter().print_workload_status()

    def emit_failure_status_snapshot(self) -> None:
        plugin = self._platform_plugin()
        if not plugin.supports_failure_status_snapshot:
            warn("Platform status snapshot at failure is not configured for this target.")
            return
        if self.kube is None or not hasattr(self.kube, "run"):
            warn("Platform status snapshot at failure is unavailable: kube client not configured.")
            return
        warn("Pod status snapshot at failure:")
        result = self.kube.run(["-n", self.cfg.namespace, "get", "pods", "-o", "wide"], check=False)
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)

