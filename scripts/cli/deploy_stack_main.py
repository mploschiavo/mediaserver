#!/usr/bin/env python3
"""Python CLI for deploy-stack orchestration."""

from __future__ import annotations

import ipaddress
import json
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.auth.provider_registry import (
    load_builtin_auth_provider_specs,
    merge_auth_provider_defaults,
)
from core.bootstrap_profile import load_bootstrap_profile_catalog
from core.edge.provider_registry import (
    compose_label_specs_by_provider,
    router_service_names_by_provider,
)
from core.phase_tracker import PhaseTracker
from core.platform_adapter import (
    RebuildPlatformAdapter,
    RebuildPlatformAdapterBuildRequest,
    build_rebuild_platform_adapter,
    normalize_platform_target,
)
from core.platform_plugin_contract import PlatformPlugin
from core.platform_plugin_registry import resolve_platform_plugin
from core.subprocess_utils import CommandResult

from cli import deploy_hook_config_resolver
from cli.bootstrap_notification_service import (
    BootstrapNotificationConfig,
    BootstrapNotificationService,
)
from cli.deploy_cli_config_service import (
    DeployStackConfig,
    parse_deploy_stack_config,
)
from cli.deploy_pipeline_service import DeployPipelineConfig, DeployPipelineService
from cli.deploy_profile_defaults_service import DeployProfileDefaultsService
from cli.deploy_script_runner_service import (
    DeployScriptRunnerConfig,
    DeployScriptRunnerService,
)

_MIN_STACK_DISK_ALLOCATION_GB = 20


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


class DeployError(RuntimeError):
    """Raised when deploy/bootstrap orchestration fails."""


class SkipPhase(RuntimeError):
    """Signal that current phase should be marked as skipped."""


@dataclass
class DeployStackRunner:
    cfg: DeployStackConfig
    kube: Any | None = None
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))
    backup_secret_values: dict[str, str] = field(default_factory=dict)
    info_fn: Callable[[str], None] = info
    _resolved_config_cache: dict[str, object] | None = field(default=None, init=False, repr=False)
    _platform_adapter_cache: RebuildPlatformAdapter | None = field(
        default=None, init=False, repr=False
    )
    _platform_plugin_cache: PlatformPlugin | None = field(default=None, init=False, repr=False)
    _platform_client_cache: dict[str, object] = field(default_factory=dict, init=False, repr=False)
    runtime_artifacts_root: Path | None = field(default=None, init=False, repr=False)
    _k8s_manifest_capture_counter: int = field(default=0, init=False, repr=False)

    def _resolved_bootstrap_config(self) -> dict[str, object]:
        if self._resolved_config_cache is None:
            payload = json.loads(self.cfg.config_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise DeployError(
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
        tuple[str, ...],
    ]:
        try:
            return deploy_hook_config_resolver.profile_actions(self._resolved_bootstrap_config())
        except ValueError as exc:
            raise DeployError(str(exc)) from exc

    def _bootstrap_job_hooks(self) -> dict[str, object]:
        cfg = self._resolved_bootstrap_config()
        adapter_hooks = cfg.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}
        bootstrap_job = adapter_hooks.get("bootstrap_job")
        if not isinstance(bootstrap_job, dict):
            return {}
        return bootstrap_job

    def _edge_hooks(self) -> dict[str, object]:
        cfg = self._resolved_bootstrap_config()
        adapter_hooks = cfg.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}
        edge = adapter_hooks.get("edge")
        if not isinstance(edge, dict):
            return {}
        return edge

    def _ingress_class_priority(self) -> tuple[str, ...]:
        hooks = self._edge_hooks()
        raw = hooks.get("ingress_class_priority")
        if not isinstance(raw, list):
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def _edge_router_provider(self) -> str:
        explicit = str(self.cfg.edge_router_provider or "").strip().lower()
        if explicit:
            return explicit
        hooks = self._edge_hooks()
        return str(hooks.get("router_provider") or "").strip().lower()

    def _edge_provider_hook_values(
        self,
        *,
        by_provider_key: str,
        fallback_key: str,
    ) -> tuple[str, ...]:
        hooks = self._edge_hooks()
        provider = self._edge_router_provider()
        values: list[str] = []
        seen: set[str] = set()

        raw_by_provider = hooks.get(by_provider_key)
        selected_from_provider_map = False
        if isinstance(raw_by_provider, dict) and provider:
            provider_values = raw_by_provider.get(provider)
            if isinstance(provider_values, list):
                selected_from_provider_map = True
                for item in provider_values:
                    token = str(item or "").strip().lower()
                    if not token or token in seen:
                        continue
                    seen.add(token)
                    values.append(token)

        if not selected_from_provider_map:
            raw_fallback = hooks.get(fallback_key)
            if isinstance(raw_fallback, list):
                for item in raw_fallback:
                    token = str(item or "").strip().lower()
                    if not token or token in seen:
                        continue
                    seen.add(token)
                    values.append(token)

        return tuple(values)

    def _edge_router_service_names(self) -> tuple[str, ...]:
        provider_defaults = router_service_names_by_provider()
        provider = self._edge_router_provider()
        out: list[str] = []
        seen: set[str] = set()
        for item in tuple(provider_defaults.get(provider) or ()):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        for item in self._edge_provider_hook_values(
            by_provider_key="router_service_names_by_provider",
            fallback_key="router_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def _edge_path_prefix_redirect_service_names(self) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for item in self._edge_provider_hook_values(
            by_provider_key="path_prefix_redirect_service_names_by_provider",
            fallback_key="path_prefix_redirect_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def _edge_compose_provider_specs(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {
            provider: dict(spec) for provider, spec in compose_label_specs_by_provider().items()
        }
        hooks = self._edge_hooks()
        raw = hooks.get("compose_provider_specs")
        if isinstance(raw, dict):
            for raw_provider, raw_spec in raw.items():
                provider = str(raw_provider or "").strip().lower()
                if not provider or not isinstance(raw_spec, dict):
                    continue
                merged_spec = dict(out.get(provider) or {})
                for raw_key, raw_value in raw_spec.items():
                    key = str(raw_key or "").strip()
                    value = str(raw_value or "").strip()
                    if key and value:
                        merged_spec[key] = value
                out[provider] = merged_spec
        return out

    def _media_server_service_names(self) -> tuple[str, ...]:
        hooks = self._edge_hooks()
        raw = hooks.get("media_server_service_names")
        out: list[str] = []
        seen: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                token = str(item or "").strip().lower()
                if not token or token in seen:
                    continue
                seen.add(token)
                out.append(token)
        if out:
            return tuple(out)
        cfg = self._resolved_bootstrap_config()
        technology_bindings = cfg.get("technology_bindings")
        if isinstance(technology_bindings, dict):
            token = str(technology_bindings.get("media_server") or "").strip().lower()
            if token:
                return (token,)
        return ()

    def _auth_provider_middleware_defaults(self) -> dict[str, str]:
        catalog = load_bootstrap_profile_catalog()
        hooks = self._edge_hooks()
        hook_defaults: dict[str, str] = {}
        raw = hooks.get("auth_provider_middleware_defaults")
        if isinstance(raw, dict):
            for raw_key, raw_value in raw.items():
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                hook_defaults[key] = str(raw_value or "").strip()

        provider_keys: list[str] = []
        seen: set[str] = set()
        for raw_key in (
            *tuple(catalog.auth_providers),
            str(catalog.auth_disabled_provider or "").strip().lower(),
            *(spec.key for spec in load_builtin_auth_provider_specs()),
            *tuple(hook_defaults.keys()),
        ):
            key = str(raw_key or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            provider_keys.append(key)
        return merge_auth_provider_defaults(
            provider_keys=tuple(provider_keys),
            catalog_defaults=dict(catalog.auth_provider_middleware_defaults or {}),
            override_defaults=hook_defaults,
        )

    def _valid_route_strategies(self) -> tuple[str, ...]:
        catalog = load_bootstrap_profile_catalog()
        values = tuple(dict.fromkeys(catalog.route_strategy_aliases.values()))
        return tuple(str(value).strip().lower() for value in values if str(value).strip())

    def _valid_auth_providers(self) -> tuple[str, ...]:
        catalog = load_bootstrap_profile_catalog()
        values: list[str] = []
        seen: set[str] = set()
        for token in (
            *tuple(catalog.auth_providers),
            str(catalog.auth_disabled_provider or "").strip().lower(),
            *tuple(self._auth_provider_middleware_defaults().keys()),
        ):
            normalized = str(token or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
        return tuple(values)

    def _valid_edge_router_providers(self) -> tuple[str, ...]:
        providers = {
            str(provider or "").strip().lower()
            for provider in self._edge_compose_provider_specs().keys()
            if str(provider or "").strip()
        }
        return tuple(sorted(providers))

    def _runtime_config_policy_handler_spec(self) -> str:
        hooks = self._bootstrap_job_hooks()
        spec = str(hooks.get("runtime_config_policy_handler") or "").strip()
        if spec and ":" not in spec:
            raise DeployError(
                "adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                "must be module.path:Symbol"
            )
        return spec

    def _runtime_config_policy_params(self) -> dict[str, object]:
        return {
            "selected_apps_csv": self.cfg.selected_apps,
            "auto_download_content": self._is_truthy(self.cfg.auto_download_content),
            "internet_exposed": self._is_truthy(self.cfg.internet_exposed),
            "route_strategy": self.cfg.route_strategy,
            "auth_provider": self.cfg.auth_provider,
            "auth_middleware": self.cfg.auth_middleware,
            "edge_router_provider": self._edge_router_provider(),
            "ingress_domain": self.cfg.ingress_domain,
            "app_gateway_host": self.cfg.app_gateway_host,
            "app_path_prefix": self.cfg.app_path_prefix,
            "media_server_direct_host": self.cfg.media_server_direct_host,
        }

    def _compose_passthrough_env_vars(self) -> tuple[str, ...]:
        env_vars: list[str] = ["STACK_ADMIN_USERNAME", "STACK_ADMIN_PASSWORD"]
        hooks = self._bootstrap_job_hooks()
        secret_targets = hooks.get("secret_priming_targets")
        if isinstance(secret_targets, dict):
            for spec in secret_targets.values():
                if not isinstance(spec, dict):
                    continue
                name = str(spec.get("env_var") or "").strip()
                if name:
                    env_vars.append(name)
        deduped: list[str] = []
        seen: set[str] = set()
        for raw_name in env_vars:
            name = str(raw_name or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return tuple(deduped)

    def _compose_preflight_handlers(self) -> tuple[str, ...]:
        try:
            return deploy_hook_config_resolver.compose_preflight_handlers(
                self._bootstrap_job_hooks()
            )
        except ValueError as exc:
            raise DeployError(str(exc)) from exc

    def _notification_service(self) -> BootstrapNotificationService:
        return BootstrapNotificationService(
            cfg=BootstrapNotificationConfig(
                alert_webhook_url=self.cfg.alert_webhook_url,
            )
        )

    def _runtime_artifacts_target_dir(self, target: str) -> Path | None:
        if self.runtime_artifacts_root is None:
            return None
        token = str(target or "").strip().lower() or "shared"
        out = self.runtime_artifacts_root / token
        out.mkdir(parents=True, exist_ok=True)
        return out

    def runtime_artifacts_target_dir(self, target: str) -> Path | None:
        return self._runtime_artifacts_target_dir(target)

    def _write_runtime_artifact_text(
        self,
        target: str,
        relative_path: str,
        text: str,
        *,
        label: str,
        log: bool = True,
    ) -> Path | None:
        base = self._runtime_artifacts_target_dir(target)
        if base is None:
            return None
        out = base / relative_path
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = text if text.endswith("\n") else f"{text}\n"
        out.write_text(payload, encoding="utf-8")
        if log:
            self.info_fn(f"{label}: {out}")
        return out

    def _write_runtime_artifact_json(
        self,
        target: str,
        relative_path: str,
        payload: dict[str, object],
        *,
        label: str,
        log: bool = True,
    ) -> Path | None:
        text = json.dumps(payload, indent=2, sort_keys=True)
        return self._write_runtime_artifact_text(
            target=target,
            relative_path=relative_path,
            text=text,
            label=label,
            log=log,
        )

    @staticmethod
    def _is_k8s_apply_with_stdin(args: list[str]) -> bool:
        tokens = [str(item or "").strip() for item in args]
        return "apply" in tokens and "-f" in tokens and "-" in tokens

    def _record_k8s_applied_manifest(self, args: list[str], manifest_text: str) -> None:
        if not manifest_text.strip():
            return
        self._k8s_manifest_capture_counter += 1
        sequence = self._k8s_manifest_capture_counter
        base_rel = f"applied-manifests/{sequence:03d}"
        self._write_runtime_artifact_text(
            target="kubernetes",
            relative_path=f"{base_rel}.yaml",
            text=manifest_text,
            label="Captured resolved Kubernetes manifest",
        )
        self._write_runtime_artifact_json(
            target="kubernetes",
            relative_path=f"{base_rel}.meta.json",
            payload={
                "captured_at": ts(),
                "phase": self.tracker.current_phase or "",
                "command": " ".join(shlex.quote(token) for token in args),
                "sequence": sequence,
            },
            label="Captured Kubernetes manifest metadata",
            log=False,
        )

    def _initialize_runtime_artifacts(self) -> None:
        target = self._resolved_platform_target()
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        namespace_token = str(self.cfg.namespace or "").strip().replace("/", "-")
        run_id = f"{timestamp}-{target}-{namespace_token}"
        root = self.cfg.root_dir / ".state" / "runtime-artifacts" / run_id
        root.mkdir(parents=True, exist_ok=True)
        self.runtime_artifacts_root = root
        self._k8s_manifest_capture_counter = 0
        self.info_fn(f"Runtime artifact root: {root}")
        self._write_runtime_artifact_json(
            target="shared",
            relative_path="run-context.json",
            payload={
                "created_at": ts(),
                "platform_target": target,
                "namespace": self.cfg.namespace,
                "profile": self.cfg.profile,
                "purpose": self.cfg.purpose,
                "bootstrap_config_file": str(self.cfg.config_file),
                "bootstrap_profile_file": (
                    str(self.cfg.bootstrap_profile_file) if self.cfg.bootstrap_profile_file else ""
                ),
                "route_strategy": self.cfg.route_strategy,
                "auth_provider": self.cfg.auth_provider,
                "edge_router_provider": self._edge_router_provider(),
                "run_bootstrap": self.cfg.run_bootstrap,
                "run_smoke_test": self.cfg.run_smoke_test,
            },
            label="Wrote runtime artifact run context",
        )

    def _script_runner_service(self) -> DeployScriptRunnerService:
        return DeployScriptRunnerService(
            cfg=DeployScriptRunnerConfig(
                root_dir=self.cfg.root_dir,
                namespace=self.cfg.namespace,
            )
        )

    def _profile_defaults_service(self) -> DeployProfileDefaultsService:
        return DeployProfileDefaultsService()

    def _pipeline_service(self) -> DeployPipelineService:
        return DeployPipelineService(
            cfg=DeployPipelineConfig(
                namespace=self.cfg.namespace,
                root_dir=self.cfg.root_dir,
                prepare_host_root=self.cfg.prepare_host_root,
                enable_components=self.cfg.enable_components,
                selected_apps=self.cfg.selected_apps,
                internet_exposed=self.cfg.internet_exposed,
                route_strategy=self.cfg.route_strategy,
                ingress_domain=self.cfg.ingress_domain,
                app_gateway_host=self.cfg.app_gateway_host,
                app_path_prefix=self.cfg.app_path_prefix,
                media_server_direct_host=self.cfg.media_server_direct_host,
                auth_provider=self.cfg.auth_provider,
                auth_middleware=self.cfg.auth_middleware,
                edge_router_provider=self._edge_router_provider(),
                preconfigure_api_keys=self.cfg.preconfigure_api_keys,
                apply_initial_preferences=self.cfg.apply_initial_preferences,
                auto_download_content=self.cfg.auto_download_content,
                config_file=self.cfg.config_file,
            ),
            info=info,
            run_script=self._run_script,
        )

    def _platform_plugin(self) -> PlatformPlugin:
        if self._platform_plugin_cache is None:
            plugin = resolve_platform_plugin(self._resolved_platform_target())
            if plugin is None:
                raise DeployError(
                    f"Unsupported platform target '{self.cfg.platform_target}'. "
                    "No platform plugin could be resolved."
                )
            self._platform_plugin_cache = plugin
        return self._platform_plugin_cache

    def _configure_platform_runtime(self) -> None:
        try:
            self._platform_plugin().configure_runner(self)
        except Exception as exc:
            raise DeployError(str(exc)) from exc

    def _platform_adapter(self) -> RebuildPlatformAdapter:
        if self._platform_adapter_cache is None:
            try:
                request_payload = self._platform_plugin().build_runner_request(self, self.info_fn)
                request = RebuildPlatformAdapterBuildRequest(**request_payload)
                self._platform_adapter_cache = build_rebuild_platform_adapter(request=request)
            except ValueError as exc:
                raise DeployError(str(exc)) from exc
        return self._platform_adapter_cache

    def get_or_create_platform_client(
        self,
        key: str,
        factory: Callable[[], object],
    ) -> object:
        token = str(key or "").strip().lower()
        if not token:
            raise DeployError("Platform client cache key cannot be empty.")
        if token not in self._platform_client_cache:
            self._platform_client_cache[token] = factory()
        return self._platform_client_cache[token]

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

    def _chaos_actions(self) -> tuple[str, ...]:
        raw = str(self.cfg.chaos_actions or "").strip()
        if not raw:
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for item in raw.split(","):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def _is_truthy(self, value: str) -> bool:
        token = str(value or "").strip().lower()
        return token in {"1", "true", "yes", "on", "y"}

    def _resolved_platform_target(self) -> str:
        resolved = normalize_platform_target(self.cfg.platform_target)
        if not resolved:
            raise DeployError("PLATFORM_TARGET cannot be empty.")
        return resolved

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
        if platform_plugin.logs_bootstrap_runner_image:
            info(f"Compose bootstrap-runner image: {self.cfg.bootstrap_runner_image}")
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
        if self.cfg.profile not in {"minimal", "full", "public-demo", "power-user"}:
            raise DeployError(
                f"Unknown PROFILE '{self.cfg.profile}'. Supported: minimal, full, public-demo, power-user."
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
                    "install a provider module under scripts/core/edge/providers/<provider>/."
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
        handled = self._platform_adapter().delete_environment_optional(self.cfg.delete_namespace)
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


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root_dir = Path(__file__).resolve().parents[2]
    cfg = parse_deploy_stack_config(args, root_dir=root_dir)
    runner = DeployStackRunner(cfg=cfg)
    try:
        return runner.run()
    except Exception as exc:
        warn(f"Deploy/bootstrap failed: {exc}")
        try:
            runner.emit_failure_status_snapshot()
        except Exception as snapshot_exc:
            warn(f"Failed collecting failure status snapshot: {snapshot_exc}")
        runner.tracker.summary()
        runner.notify(
            "error",
            f"media-stack deploy/bootstrap failed (profile={cfg.profile}, namespace={cfg.namespace})",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
