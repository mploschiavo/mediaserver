#!/usr/bin/env python3
"""Run the full media-stack bootstrap Kubernetes job."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import importlib
import inspect
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from media_stack.services.top_level_config_model import TopLevelBootstrapConfig
from media_stack.core.exceptions import ConfigError, MediaStackError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient

from media_stack.services.controller_component_resolver import resolve_bootstrap_component_plan
from media_stack.cli.workflows.controller_core_phases_service import (
    ControllerCorePhasesConfig,
    ControllerCorePhasesService,
)
from media_stack.cli.workflows.controller_deployment_ops_service import (
    ControllerDeploymentOpsConfig,
    ControllerDeploymentOpsService,
)
from media_stack.cli.workflows.controller_job_artifacts_service import (
    ControllerJobArtifacts,
    ControllerJobArtifactsService,
)
from media_stack.cli.workflows.controller_job_logs_service import (
    ControllerJobLogsConfig,
    ControllerJobLogsService,
)
from media_stack.cli.workflows.controller_job_wait_service import ControllerJobWaitConfig, ControllerJobWaitService
from media_stack.cli.workflows.controller_manifest_service import ControllerManifestConfig, ControllerManifestService
from media_stack.cli.workflows.controller_notification_service import (
    ControllerNotificationConfig,
    ControllerNotificationService,
)
from media_stack.cli.workflows.controller_post_job_actions_service import (
    ControllerPostJobAction,
    ControllerPostJobActionsService,
)
from media_stack.cli.workflows.controller_script_runner_service import (
    ControllerScriptRunnerConfig,
    ControllerScriptRunnerService,
)
from media_stack.cli.workflows.controller_secret_priming_service import (
    ControllerSecretPrimingConfig,
    ControllerSecretPrimingService,
)
from media_stack.cli.workflows.controller_secret_reader_service import (
    ControllerSecretReaderConfig,
    ControllerSecretReaderService,
)
from media_stack.cli.workflows.run_controller_job_cli_config_service import (
    RunBootstrapJobConfig,
    parse_run_bootstrap_job_config,
)


from media_stack.core.cli_common import PhaseTracker, err, info, ts, warn  # noqa: E402
import logging

from media_stack.cli.commands.run_controller_job_priming_mixin import _RunBootstrapJobPrimingMixin


class RunBootstrapJobRunner(_RunBootstrapJobPrimingMixin):
    def __init__(
        self, cfg: RunBootstrapJobConfig, kube: KubernetesClient, tracker: PhaseTracker
    ) -> None:
        self.cfg = cfg
        self.kube = kube
        self.tracker = tracker
        self.artifacts_service = ControllerJobArtifactsService()
        self.artifacts: ControllerJobArtifacts = self.artifacts_service.create()
        self._resolved_cfg_cache: dict[str, object] | None = None

    def _job_wait_service(self) -> ControllerJobWaitService:
        return ControllerJobWaitService(
            cfg=ControllerJobWaitConfig(
                namespace=self.cfg.namespace,
                timeout_seconds=self.cfg.timeout_seconds,
                timeout_raw=self.cfg.timeout_raw,
                heartbeat_interval=self.cfg.heartbeat_interval,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _secret_priming_service(self) -> ControllerSecretPrimingService:
        return ControllerSecretPrimingService(
            cfg=ControllerSecretPrimingConfig(
                namespace=self.cfg.namespace,
                bootstrap_config_file=self.artifacts.job_config_file,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _manifest_service(self) -> ControllerManifestService:
        return ControllerManifestService(
            cfg=ControllerManifestConfig(
                namespace=self.cfg.namespace,
                root_dir=self.cfg.root_dir,
                prepare_host_root=self.cfg.prepare_host_root,
                bootstrap_runner_image=self.cfg.bootstrap_runner_image,
                job_config_file=self.artifacts.job_config_file,
                bootstrap_profile_file=self.cfg.bootstrap_profile_file,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _notification_service(self) -> ControllerNotificationService:
        return ControllerNotificationService(
            cfg=ControllerNotificationConfig(
                alert_webhook_url=self.cfg.alert_webhook_url,
            )
        )

    def _script_runner_service(self) -> ControllerScriptRunnerService:
        return ControllerScriptRunnerService(
            cfg=ControllerScriptRunnerConfig(root_dir=self.cfg.root_dir),
        )

    def _deployment_ops_service(self) -> ControllerDeploymentOpsService:
        return ControllerDeploymentOpsService(
            cfg=ControllerDeploymentOpsConfig(namespace=self.cfg.namespace),
            kube=self.kube,
            info=info,
        )

    def _secret_reader_service(self) -> ControllerSecretReaderService:
        return ControllerSecretReaderService(
            cfg=ControllerSecretReaderConfig(namespace=self.cfg.namespace),
            kube=self.kube,
        )

    def _post_job_actions_service(self) -> ControllerPostJobActionsService:
        return ControllerPostJobActionsService(actions=self._resolve_post_job_actions())

    def _core_phases_service(self) -> ControllerCorePhasesService:
        return ControllerCorePhasesService(
            ControllerCorePhasesConfig(
                config_file=self.cfg.config_file,
                namespace=self.cfg.namespace,
                prepare_host_root=self.cfg.prepare_host_root,
                phase_skip_flags=self.cfg.effective_phase_skip_flags,
            )
        )

    def _job_logs_service(self) -> ControllerJobLogsService:
        return ControllerJobLogsService(
            cfg=ControllerJobLogsConfig(
                namespace=self.cfg.namespace,
                job_name="media-stack-controller",
                log_file=self.artifacts.job_log_file,
                tail_lines=self.cfg.job_log_tail_lines,
            ),
            kube=self.kube,
        )

    def _resolved_cfg(self) -> dict[str, object]:
        if self._resolved_cfg_cache is None:
            self._resolved_cfg_cache = resolve_bootstrap_component_plan(self.cfg.config_file).config
        return self._resolved_cfg_cache

    def _bootstrap_job_hooks(self) -> dict[str, object]:
        adapter_hooks = self._resolved_cfg().get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}
        bootstrap_job = adapter_hooks.get("bootstrap_job")
        if not isinstance(bootstrap_job, dict):
            return {}
        return bootstrap_job

    def _resolve_post_job_actions(self) -> list[ControllerPostJobAction]:
        hooks = self._bootstrap_job_hooks()
        raw_actions = hooks.get("post_job_actions")
        if raw_actions is None:
            return []
        if not isinstance(raw_actions, list):
            raise ConfigError("adapter_hooks.bootstrap_job.post_job_actions must be an array")

        actions: list[ControllerPostJobAction] = []
        for idx, item in enumerate(raw_actions):
            if not isinstance(item, dict):
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.post_job_actions" f"[{idx}] must be an object"
                )
            marker = str(item.get("marker") or "").strip()
            phase_name = str(item.get("phase_name") or "").strip()
            deployment = str(item.get("deployment") or "").strip()
            if not marker or not phase_name or not deployment:
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.post_job_actions"
                    f"[{idx}] requires marker, phase_name, and deployment"
                )
            timeout_seconds = int(item.get("timeout_seconds") or 180)
            restart_if_exists = bool(item.get("restart_if_exists", True))
            actions.append(
                ControllerPostJobAction(
                    marker=marker,
                    phase_name=phase_name,
                    deployment=deployment,
                    timeout_seconds=timeout_seconds,
                    restart_if_exists=restart_if_exists,
                )
            )
        return actions

    def _resolve_call_handler_specs(self) -> dict[str, str]:
        out: dict[str, str] = {}

        # 1. Load from per-service YAML plugin.call_handlers
        try:
            from media_stack.core.service_registry.registry import _find_services_dir
            import yaml
            svc_dir = _find_services_dir()
            if svc_dir:
                for yaml_file in sorted(svc_dir.glob("*.yaml")):
                    if yaml_file.name.startswith("_"):
                        continue
                    try:
                        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                        call_handlers = (data.get("plugin") or {}).get("call_handlers")
                        if isinstance(call_handlers, dict):
                            for key, spec in call_handlers.items():
                                k = str(key or "").strip()
                                s = str(spec or "").strip()
                                if k and s and ":" in s:
                                    out[k] = s
                    except Exception as exc:
                        log_swallowed(exc)
        except Exception as exc:
            log_swallowed(exc)

        # 2. Fill gaps from config.json (backward compat)
        hooks = self._bootstrap_job_hooks()
        raw_map = hooks.get("call_handlers")
        if isinstance(raw_map, dict):
            for key, spec in raw_map.items():
                handler_key = str(key or "").strip()
                hook_spec = str(spec or "").strip()
                if handler_key and hook_spec and handler_key not in out:
                    if ":" not in hook_spec:
                        raise ConfigError(
                            "adapter_hooks.bootstrap_job.call_handlers"
                            f".{handler_key} must be module.path:Symbol"
                        )
                    out[handler_key] = hook_spec
        return out

    @staticmethod
    def _import_hook(spec: str) -> Callable[..., None]:
        module_name, symbol_name = spec.split(":", 1)
        module = importlib.import_module(module_name)
        hook = getattr(module, symbol_name, None)
        if not callable(hook):
            raise ConfigError(f"Hook '{spec}' did not resolve to a callable")
        return hook

    def _hook_context(self) -> dict[str, object]:
        return {
            "namespace": self.cfg.namespace,
            "kube": self.kube,
            "info": info,
            "warn": warn,
            "deployment_exists": self.deployment_exists,
            "restart_deployment": (
                lambda deployment, timeout_seconds=180: self.restart_deployment(
                    deployment,
                    timeout_seconds=int(timeout_seconds),
                )
            ),
            "restart_deployment_if_exists": (
                lambda deployment, timeout_seconds=180: self.restart_deployment_if_exists(
                    deployment,
                    timeout_seconds=int(timeout_seconds),
                )
            ),
            "read_secret_key": self._read_secret_key,
            "log_contains": self._log_contains,
        }

    def _invoke_hook_with_context(
        self,
        hook: Callable[..., None],
        *,
        hook_name: str,
        context: dict[str, object],
    ) -> None:
        signature = inspect.signature(hook)
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
        )
        if accepts_kwargs:
            hook(**context)
            return

        accepted = {key: value for key, value in context.items() if key in signature.parameters}
        required_missing = [
            name
            for name, param in signature.parameters.items()
            if param.default is inspect.Parameter.empty
            and param.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            and name not in accepted
        ]
        if required_missing:
            raise ConfigError(
                f"Hook '{hook_name}' requires unsupported parameters: {', '.join(required_missing)}"
            )
        hook(**accepted)

    def _invoke_hook(self, hook: Callable[..., None], *, hook_name: str) -> None:
        self._invoke_hook_with_context(
            hook,
            hook_name=hook_name,
            context=self._hook_context(),
        )

    def _runtime_config_policy_handler_spec(self) -> str:
        hooks = self._bootstrap_job_hooks()
        spec = str(hooks.get("runtime_config_policy_handler") or "").strip()
        if spec and ":" not in spec:
            raise ConfigError(
                "adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                "must be module.path:Symbol"
            )
        return spec

    def _apply_runtime_config_policy(self, cfg: dict[str, object]) -> None:
        spec = self._runtime_config_policy_handler_spec()
        if not spec:
            raise ConfigError(
                "adapter_hooks.bootstrap_job.runtime_config_policy_handler must be set"
            )
        hook = self._import_hook(spec)
        self._invoke_hook_with_context(
            hook,
            hook_name="apply_runtime_config_policy",
            context={
                "cfg": cfg,
                "selected_apps_csv": self.cfg.selected_apps,
                "auto_download_content": self.cfg.auto_download_content,
                "internet_exposed": self.cfg.internet_exposed,
                "route_strategy": self.cfg.route_strategy,
                "ingress_domain": self.cfg.ingress_domain,
                "app_gateway_host": self.cfg.app_gateway_host,
                "app_path_prefix": self.cfg.app_path_prefix,
                "media_server_direct_host": self.cfg.media_server_direct_host,
            },
        )

    def run(self) -> int:
        if not self.cfg.config_file.exists():
            raise ConfigError(f"Config file not found: {self.cfg.config_file}")

        info(f"Namespace: {self.cfg.namespace}")
        info(f"Config: {self.cfg.config_file}")
        info(f"Ingress: {self.cfg.ingress_name}")
        info(f"Bootstrap runner image: {self.cfg.bootstrap_runner_image}")
        info(f"Heartbeat interval: {self.cfg.heartbeat_interval}s")
        self.notify("info", f"media-stack bootstrap job started (namespace={self.cfg.namespace})")

        try:
            operation_handlers: dict[str, Callable[[], None]] = {
                "prepare_bootstrap_job_config": self.prepare_bootstrap_job_config,
                "ensure_bootstrap_pvc_prereqs": self.ensure_bootstrap_pvc_prereqs,
                "prime_servarr_api_keys_secret": self.prime_servarr_api_keys_secret,
                "prime_usenet_client_api_key_secret": self.prime_usenet_client_api_key_secret,
                "prime_request_manager_api_key_secret": self.prime_request_manager_api_key_secret,
                "prime_analytics_api_key_secret": self.prime_analytics_api_key_secret,
                "prime_media_server_api_key_secret": self.prime_media_server_api_key_secret,
                "prime_media_server_user_id_secret": self.prime_media_server_user_id_secret,
                "update_bootstrap_configmaps": self.update_bootstrap_configmaps,
                # Deployment-based (preferred).
                "ensure_bootstrap_deployment": self.ensure_bootstrap_deployment,
                "wait_for_bootstrap_service": self.wait_for_bootstrap_service,
                # Legacy Job-based (backward compatible).
                "recreate_bootstrap_job": self.recreate_bootstrap_job,
                "wait_for_bootstrap_job": self.wait_for_bootstrap_job,
                "print_bootstrap_job_logs": self.print_bootstrap_job_logs,
            }

            for handler_key, spec in self._resolve_call_handler_specs().items():
                hook = self._import_hook(spec)
                operation_handlers[handler_key] = (
                    lambda imported=hook, name=handler_key: self._invoke_hook(
                        imported,
                        hook_name=name,
                    )
                )

            self._core_phases_service().run(
                run_phase=self._run_phase,
                run_script=self._run_script,
                operation_handlers=operation_handlers,
            )

            self._post_job_actions_service().run_actions(
                log_contains=self._log_contains,
                run_phase=self._run_phase,
                restart_deployment=lambda deployment: self.restart_deployment(
                    deployment,
                    timeout_seconds=180,
                ),
                restart_deployment_if_exists=lambda deployment: self.restart_deployment_if_exists(
                    deployment,
                    timeout_seconds=180,
                ),
            )

            info("Bootstrap job completed.")
            self.tracker.print_summary()
            self.notify(
                "ok", f"media-stack bootstrap job completed (namespace={self.cfg.namespace})"
            )
            return 0
        except Exception:
            self.notify(
                "error", f"media-stack bootstrap job failed (namespace={self.cfg.namespace})"
            )
            raise
        finally:
            self.cleanup()

    def _run_phase(self, phase_name: str, fn: Callable[[], None], *, enabled: bool = True) -> None:
        self.tracker.start(phase_name)
        if not enabled:
            self.tracker.end("skipped")
            return
        try:
            fn()
            self.tracker.end("ok")
        except Exception:
            self.tracker.end("failed")
            raise

    def cleanup(self) -> None:
        self.artifacts_service.cleanup(self.artifacts)

    def notify(self, status: str, message: str) -> None:
        self._notification_service().notify(status, message)

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        self._script_runner_service().run_script(script_name, *args, env=env)

    def manifest_overrides(self, text: str) -> str:
        return self._manifest_service().manifest_overrides(text)

    def ensure_bootstrap_pvc_prereqs(self) -> None:
        self._manifest_service().ensure_bootstrap_pvc_prereqs()

    def prepare_bootstrap_job_config(self) -> None:
        payload = json.loads(self.cfg.config_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ConfigError(f"Expected JSON object in {self.cfg.config_file}")
        try:
            cfg = TopLevelBootstrapConfig.from_dict(payload).to_dict()
        except ValueError as exc:
            raise ConfigError(f"Invalid bootstrap config at {self.cfg.config_file}: {exc}") from exc
        info(
            f"Config policy inputs: route_strategy={self.cfg.route_strategy}, "
            f"gateway_host={self.cfg.app_gateway_host}, "
            f"path_prefix={self.cfg.app_path_prefix}, "
            f"ingress_domain={self.cfg.ingress_domain}, "
            f"media_server_direct={self.cfg.media_server_direct_host}"
        )
        self._apply_runtime_config_policy(cfg)
        self.artifacts.job_config_file.write_text(
            json.dumps(cfg, indent=2) + "\n",
            encoding="utf-8",
        )
        # Log key config values after policy application.
        from media_stack.services.apps.stack.config_diagnostics import log_config_policy_values
        log_config_policy_values(cfg, info)
        info(
            "Bootstrap preconfigure flags: "
            f"api_keys={'on' if self.cfg.preconfigure_api_keys else 'off'}, "
            f"initial_preferences={'on' if self.cfg.apply_initial_preferences else 'off'}"
        )
        info(
            "Bootstrap content mode: "
            f"{'automatic download enabled' if self.cfg.auto_download_content else 'manual download mode'}"
        )
        info(f"Prepared bootstrap job config: {self.artifacts.job_config_file}")

    # prime_*_secret, update_bootstrap_configmaps, recreate_bootstrap_job,
    # and ensure_bootstrap_deployment live on ``_RunBootstrapJobPrimingMixin``
    # (see its module for the bodies).

    def wait_for_bootstrap_service(self) -> None:
        import time as _time

        wait_svc = self._job_wait_service()
        port = wait_svc.cfg.service_port

        # Find a ready bootstrap pod with HTTP server responding.
        info("Waiting for bootstrap service pod...")
        pod_name = None
        deadline = _time.time() + 120
        while _time.time() < deadline:
            pod_name = wait_svc._find_bootstrap_pod(selector="app=media-stack-controller")
            if pod_name:
                status = wait_svc._query_bootstrap_status(pod_name)
                if status is not None:
                    info(f"Bootstrap service responding on pod {pod_name}")
                    break
                pod_name = None
            _time.sleep(3)

        if not pod_name:
            raise ConfigError("Bootstrap service pod not found or not responding within 120s")

        # Trigger the bootstrap action via HTTP.
        info(f"Triggering bootstrap action on pod {pod_name}")
        trigger_script = (
            "import urllib.request,json; "
            "req=urllib.request.Request("
            f"'http://127.0.0.1:{port}/actions/bootstrap',"
            "data=b'{}',"
            "headers={'Content-Type':'application/json'}); "
            "r=urllib.request.urlopen(req,timeout=10); "
            "print(r.read().decode())"
        )
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", pod_name, "--",
             "python3", "-c", trigger_script],
            check=False,
        )
        if result.stdout:
            info(f"Bootstrap trigger response: {result.stdout.strip()}")
        if result.returncode != 0:
            warn(f"Bootstrap trigger may have failed: {result.stderr or 'unknown error'}")

        # Wait for completion via HTTP polling.
        wait_svc.wait_for_bootstrap_service(
            selector="app=media-stack-controller",
        )

    # wait_for_bootstrap_job, print_bootstrap_job_logs, _log_contains,
    # deployment_exists, restart_deployment, restart_deployment_if_exists,
    # and _read_secret_key live on ``_RunBootstrapJobPrimingMixin``.


class RunBootstrapJobEntryPoint:
    """CLI entry-point wrapper for ``RunBootstrapJobRunner`` (ADR-0012).

    The previously module-level ``main`` helper is folded onto this
    class as the plain instance method ``main`` (no ``@staticmethod``).
    The module-level ``_INSTANCE`` singleton aliases ``main`` so the
    historical entry-point surface (``python -m
    media_stack.cli.commands.run_controller_job_main`` and
    ``from … import main``) keeps resolving unchanged.
    """

    def main(self, argv: list[str] | None = None) -> int:
        root_dir = Path(__file__).resolve().parents[2]
        cfg = parse_run_bootstrap_job_config(argv, root_dir=root_dir)
        runner = RunBootstrapJobRunner(
            cfg=cfg, kube=KubernetesClient.from_environment(), tracker=PhaseTracker()
        )
        return runner.run()


# Module-level singleton + aliases (ADR-0012 pattern).
_INSTANCE = RunBootstrapJobEntryPoint()

main = _INSTANCE.main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MediaStackError as exc:
        err(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        err("Interrupted.")
        raise SystemExit(130)
