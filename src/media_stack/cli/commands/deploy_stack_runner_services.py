"""Service factories, runtime artifacts, and utility helpers for DeployStackRunner.

Extracted from deploy_stack_main.py — methods that build service instances,
manage runtime artifact capture, and provide utility helpers.
"""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from media_stack.core.auth.provider_registry import compose_service_names_by_provider
from media_stack.core.platform_adapter import (
    RebuildPlatformAdapter,
    RebuildPlatformAdapterBuildRequest,
    build_rebuild_platform_adapter,
    normalize_platform_target,
)
from media_stack.core.platform_plugin_contract import PlatformPlugin
from media_stack.core.platform_plugin_registry import resolve_platform_plugin

from media_stack.cli.commands.deploy_stack_errors import DeployError
from media_stack.core.cli_common import info, ts
from media_stack.cli.workflows.controller_notification_service import (
    ControllerNotificationConfig,
    ControllerNotificationService,
)
from media_stack.cli.workflows.deploy_pipeline_service import DeployPipelineConfig, DeployPipelineService
from media_stack.cli.workflows.deploy_profile_defaults_service import DeployProfileDefaultsService
from media_stack.cli.workflows.deploy_script_runner_service import (
    DeployScriptRunnerConfig,
    DeployScriptRunnerService,
)

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import DeployStackConfig
    from media_stack.core.phase_tracker import PhaseTracker


class RunnerServicesMixin:
    """Service factories, artifact management, and utility helpers.

    Requires on the concrete class:
    - ``self.cfg: DeployStackConfig``
    - ``self.info_fn: Callable[[str], None]``
    - ``self.runtime_artifacts_root: Path | None``
    - ``self._k8s_manifest_capture_counter: int``
    - ``self.tracker: PhaseTracker``
    - ``self._platform_plugin_cache: PlatformPlugin | None``
    - ``self._platform_adapter_cache: RebuildPlatformAdapter | None``
    - ``self._platform_client_cache: dict[str, object]``
    - ``self._delete_environment_enabled_cache: bool | None``
    """

    cfg: DeployStackConfig
    info_fn: Callable[[str], None]
    runtime_artifacts_root: Path | None
    _k8s_manifest_capture_counter: int
    tracker: PhaseTracker
    _platform_plugin_cache: PlatformPlugin | None
    _platform_adapter_cache: RebuildPlatformAdapter | None
    _platform_client_cache: dict[str, object]
    _delete_environment_enabled_cache: bool | None

    # -- service factories -------------------------------------------------

    def _notification_service(self) -> ControllerNotificationService:
        return ControllerNotificationService(
            cfg=ControllerNotificationConfig(
                alert_webhook_url=self.cfg.alert_webhook_url,
            )
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
                app_gateway_port=self.cfg.app_gateway_port,
                app_path_prefix=self.cfg.app_path_prefix,
                media_server_direct_host=self.cfg.media_server_direct_host,
                auth_provider=self.cfg.auth_provider,
                auth_middleware=self.cfg.auth_middleware,
                edge_router_provider=self._edge_router_provider(),
                preconfigure_api_keys=self.cfg.preconfigure_api_keys,
                apply_initial_preferences=self.cfg.apply_initial_preferences,
                auto_download_content=self.cfg.auto_download_content,
                config_file=self.cfg.config_file,
                platform_target=self._resolved_platform_target(),
                bootstrap_profile_file=str(self.cfg.bootstrap_profile_file or ""),
            ),
            info=info,
            run_script=self._run_script,
        )

    # -- platform plugin / adapter -----------------------------------------

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

    # -- runtime artifacts -------------------------------------------------

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

    # -- utility helpers ---------------------------------------------------

    def _compose_profiles(self) -> tuple[str, ...]:
        raw = str(self.cfg.compose_profiles or "").strip()
        if not raw:
            return ()
        return tuple(token.strip() for token in raw.split(",") if token.strip())

    def _selected_apps(self) -> tuple[str, ...]:
        raw = str(self.cfg.selected_apps or "").strip()
        out: list[str] = []
        seen: set[str] = set()
        for item in raw.split(",") if raw else ():
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)

        for service_name in self._auth_provider_service_names():
            token = str(service_name or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def _auth_provider_service_names(self) -> tuple[str, ...]:
        provider = str(self.cfg.auth_provider or "").strip().lower()
        if not provider or provider == "none":
            return ()
        service_names = tuple(compose_service_names_by_provider().get(provider) or ())
        out: list[str] = []
        seen: set[str] = set()
        for item in service_names:
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

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

    def _delete_environment_requested(self) -> bool:
        return self._is_truthy(self.cfg.delete_namespace)

    def _delete_environment_confirmation_target(self) -> str:
        target = self._resolved_platform_target()
        if target == "compose":
            candidate = str(self.cfg.compose_project_name or "").strip()
            if candidate:
                return candidate
        return str(self.cfg.namespace or "").strip()

    def _delete_environment_enabled(self) -> bool:
        if self._delete_environment_enabled_cache is not None:
            return self._delete_environment_enabled_cache
        if not self._delete_environment_requested():
            self._delete_environment_enabled_cache = False
            return False
        confirmation = str(self.cfg.delete_namespace_confirm or "").strip()
        confirmation_target = self._delete_environment_confirmation_target()
        if confirmation == "I_UNDERSTAND":
            self._delete_environment_enabled_cache = True
            return True
        if confirmation and confirmation_target and confirmation == confirmation_target:
            self._delete_environment_enabled_cache = True
            return True
        from media_stack.core.cli_common import warn
        warn(
            "Delete namespace requested but blocked by safeguard. "
            "Set DELETE_NAMESPACE_CONFIRM to the environment identifier "
            f"('{confirmation_target}') or 'I_UNDERSTAND' to allow teardown."
        )
        self._delete_environment_enabled_cache = False
        return False

    def _resolved_platform_target(self) -> str:
        resolved = normalize_platform_target(self.cfg.platform_target)
        if not resolved:
            raise DeployError("PLATFORM_TARGET cannot be empty.")
        return resolved

