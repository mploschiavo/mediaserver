#!/usr/bin/env python3
"""Run the full media-stack bootstrap Kubernetes job."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from bootstrap_services.apps.jellyfin.cli.jellyfin_plugin_activation_service import (
    JellyfinPluginActivationConfig,
    JellyfinPluginActivationService,
)
from bootstrap_services.top_level_config_model import TopLevelBootstrapConfig
from core.exceptions import ConfigError, MediaStackError
from core.kube import KubernetesClient

from cli.bootstrap_core_phases_service import (
    BootstrapCorePhasesConfig,
    BootstrapCorePhasesService,
)
from cli.bootstrap_deployment_ops_service import (
    BootstrapDeploymentOpsConfig,
    BootstrapDeploymentOpsService,
)
from cli.bootstrap_job_artifacts_service import (
    BootstrapJobArtifacts,
    BootstrapJobArtifactsService,
)
from cli.bootstrap_job_logs_service import (
    BootstrapJobLogsConfig,
    BootstrapJobLogsService,
)
from cli.bootstrap_job_wait_service import BootstrapJobWaitConfig, BootstrapJobWaitService
from cli.bootstrap_manifest_service import BootstrapManifestConfig, BootstrapManifestService
from cli.bootstrap_notification_service import (
    BootstrapNotificationConfig,
    BootstrapNotificationService,
)
from cli.bootstrap_post_job_actions_service import BootstrapPostJobActionsService
from cli.bootstrap_script_runner_service import (
    BootstrapScriptRunnerConfig,
    BootstrapScriptRunnerService,
)
from cli.bootstrap_secret_priming_service import (
    BootstrapSecretPrimingConfig,
    BootstrapSecretPrimingService,
)
from cli.bootstrap_secret_reader_service import (
    BootstrapSecretReaderConfig,
    BootstrapSecretReaderService,
)
from cli.run_bootstrap_job_cli_config_service import (
    RunBootstrapJobConfig,
    parse_run_bootstrap_job_config,
)


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


@dataclass
class PhaseTracker:
    run_start_epoch: int = field(default_factory=lambda: int(time.time()))
    current_phase: str = ""
    current_phase_start: int = 0
    phase_names: list[str] = field(default_factory=list)
    phase_results: list[str] = field(default_factory=list)
    phase_seconds: list[int] = field(default_factory=list)

    def start(self, phase_name: str) -> None:
        self.current_phase = phase_name
        self.current_phase_start = int(time.time())
        info(f"[PHASE] START: {phase_name}")

    def end(self, result: str = "ok") -> None:
        now = int(time.time())
        if self.current_phase and self.current_phase_start > 0:
            elapsed = now - self.current_phase_start
            self.phase_names.append(self.current_phase)
            self.phase_results.append(result)
            self.phase_seconds.append(elapsed)
            if result == "ok":
                info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_phase_start = 0

    def print_summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        info(f"Phase Summary (total {total}s)")
        if not self.phase_names:
            info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.phase_names):
            info(f"  {name} => {self.phase_results[idx]} ({self.phase_seconds[idx]}s)")


class RunBootstrapJobRunner:
    def __init__(
        self, cfg: RunBootstrapJobConfig, kube: KubernetesClient, tracker: PhaseTracker
    ) -> None:
        self.cfg = cfg
        self.kube = kube
        self.tracker = tracker
        self.artifacts_service = BootstrapJobArtifactsService()
        self.artifacts: BootstrapJobArtifacts = self.artifacts_service.create()

    def _job_wait_service(self) -> BootstrapJobWaitService:
        return BootstrapJobWaitService(
            cfg=BootstrapJobWaitConfig(
                namespace=self.cfg.namespace,
                timeout_seconds=self.cfg.timeout_seconds,
                timeout_raw=self.cfg.timeout_raw,
                heartbeat_interval=self.cfg.heartbeat_interval,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _secret_priming_service(self) -> BootstrapSecretPrimingService:
        return BootstrapSecretPrimingService(
            cfg=BootstrapSecretPrimingConfig(
                namespace=self.cfg.namespace,
                bootstrap_config_file=self.artifacts.job_config_file,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _manifest_service(self) -> BootstrapManifestService:
        return BootstrapManifestService(
            cfg=BootstrapManifestConfig(
                namespace=self.cfg.namespace,
                root_dir=self.cfg.root_dir,
                prepare_host_root=self.cfg.prepare_host_root,
                bootstrap_runner_image=self.cfg.bootstrap_runner_image,
                job_config_file=self.artifacts.job_config_file,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _jellyfin_plugin_service(self) -> JellyfinPluginActivationService:
        return JellyfinPluginActivationService(
            cfg=JellyfinPluginActivationConfig(namespace=self.cfg.namespace),
            kube=self.kube,
            info=info,
            warn=warn,
            deployment_exists=self.deployment_exists,
            restart_deployment=lambda deployment, timeout_seconds: self.restart_deployment(
                deployment,
                timeout_seconds=timeout_seconds,
            ),
            read_secret_key=self._read_secret_key,
        )

    def _notification_service(self) -> BootstrapNotificationService:
        return BootstrapNotificationService(
            cfg=BootstrapNotificationConfig(
                alert_webhook_url=self.cfg.alert_webhook_url,
            )
        )

    def _script_runner_service(self) -> BootstrapScriptRunnerService:
        return BootstrapScriptRunnerService(
            cfg=BootstrapScriptRunnerConfig(root_dir=self.cfg.root_dir),
        )

    def _deployment_ops_service(self) -> BootstrapDeploymentOpsService:
        return BootstrapDeploymentOpsService(
            cfg=BootstrapDeploymentOpsConfig(namespace=self.cfg.namespace),
            kube=self.kube,
            info=info,
        )

    def _secret_reader_service(self) -> BootstrapSecretReaderService:
        return BootstrapSecretReaderService(
            cfg=BootstrapSecretReaderConfig(namespace=self.cfg.namespace),
            kube=self.kube,
        )

    def _post_job_actions_service(self) -> BootstrapPostJobActionsService:
        return BootstrapPostJobActionsService()

    def _core_phases_service(self) -> BootstrapCorePhasesService:
        return BootstrapCorePhasesService(
            BootstrapCorePhasesConfig(
                config_file=self.cfg.config_file,
                namespace=self.cfg.namespace,
                prepare_host_root=self.cfg.prepare_host_root,
                phase_skip_flags=self.cfg.effective_phase_skip_flags,
            )
        )

    def _job_logs_service(self) -> BootstrapJobLogsService:
        return BootstrapJobLogsService(
            cfg=BootstrapJobLogsConfig(
                namespace=self.cfg.namespace,
                job_name="media-stack-bootstrap",
                log_file=self.artifacts.job_log_file,
                tail_lines=self.cfg.job_log_tail_lines,
            ),
            kube=self.kube,
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
            self._core_phases_service().run(
                run_phase=self._run_phase,
                run_script=self._run_script,
                operation_handlers={
                    "prepare_bootstrap_job_config": self.prepare_bootstrap_job_config,
                    "ensure_bootstrap_pvc_prereqs": self.ensure_bootstrap_pvc_prereqs,
                    "prime_servarr_api_keys_secret": self.prime_servarr_api_keys_secret,
                    "prime_usenet_client_api_key_secret": self.prime_usenet_client_api_key_secret,
                    "prime_request_manager_api_key_secret": self.prime_request_manager_api_key_secret,
                    "prime_tautulli_api_key_secret": self.prime_tautulli_api_key_secret,
                    "update_bootstrap_configmaps": self.update_bootstrap_configmaps,
                    "recreate_bootstrap_job": self.recreate_bootstrap_job,
                    "wait_for_bootstrap_job": self.wait_for_bootstrap_job,
                    "print_bootstrap_job_logs": self.print_bootstrap_job_logs,
                },
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

            self._run_phase(
                "Activate Jellyfin plugins (restart if needed)",
                self.activate_jellyfin_plugins,
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
        self.artifacts.job_config_file.write_text(
            json.dumps(cfg, indent=2) + "\n",
            encoding="utf-8",
        )
        info(f"Prepared bootstrap job config: {self.artifacts.job_config_file}")

    def prime_servarr_api_keys_secret(self) -> None:
        self._secret_priming_service().prime_servarr_api_keys()

    def prime_usenet_client_api_key_secret(self) -> None:
        self._secret_priming_service().prime_sab_api_key()

    def prime_request_manager_api_key_secret(self) -> None:
        self._secret_priming_service().prime_jellyseerr_api_key()

    def prime_tautulli_api_key_secret(self) -> None:
        self._secret_priming_service().prime_tautulli_api_key()

    def update_bootstrap_configmaps(self) -> None:
        self._manifest_service().update_bootstrap_configmaps()

    def recreate_bootstrap_job(self) -> None:
        self._manifest_service().recreate_bootstrap_job()

    def wait_for_bootstrap_job(self) -> None:
        self._job_wait_service().wait_for_job(
            job_name="media-stack-bootstrap",
            selector="app=media-stack-bootstrap",
        )

    def print_bootstrap_job_logs(self) -> None:
        self._job_logs_service().capture_logs()

    def _log_contains(self, marker: str) -> bool:
        return self._job_logs_service().log_contains(marker)

    def deployment_exists(self, deployment: str) -> bool:
        return self._deployment_ops_service().deployment_exists(deployment)

    def restart_deployment(self, deployment: str, *, timeout_seconds: int) -> None:
        self._deployment_ops_service().restart_deployment(
            deployment,
            timeout_seconds=timeout_seconds,
        )

    def restart_deployment_if_exists(self, deployment: str, *, timeout_seconds: int) -> None:
        self._deployment_ops_service().restart_deployment_if_exists(
            deployment,
            timeout_seconds=timeout_seconds,
        )

    def _read_secret_key(self, secret: str, key_name: str) -> str:
        return self._secret_reader_service().read_secret_key(secret, key_name)

    def activate_jellyfin_plugins(self) -> None:
        self._jellyfin_plugin_service().activate_plugins_if_needed()


def main(argv: list[str] | None = None) -> int:
    root_dir = Path(__file__).resolve().parents[2]
    cfg = parse_run_bootstrap_job_config(argv, root_dir=root_dir)
    runner = RunBootstrapJobRunner(
        cfg=cfg, kube=KubernetesClient.from_environment(), tracker=PhaseTracker()
    )
    return runner.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MediaStackError as exc:
        err(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        err("Interrupted.")
        raise SystemExit(130)
