"""RunBootstrapJobPipeline — Composition Root + Template Method.

ADR-0015 Phase 7c. The pre-Phase-7c
:class:`RunBootstrapJobRunner` god class (604 LoC, 30+ methods,
inherited from a 75-LoC :class:`_RunBootstrapJobPrimingMixin`)
was the K8s mirror of the pre-Phase-4 ``DeployStackRunner``
anti-pattern.

Phase 7c applies the Phase 4 template: the runner becomes a thin
Composition Root that wires SRP collaborators
(:class:`BootstrapJobServiceBundle`,
:class:`BootstrapJobConfigResolver`,
:class:`BootstrapHookDispatcher`,
:class:`BootstrapPrimingPhase`,
:class:`BootstrapPhase`) and owns the dispatch loop in
:meth:`run`. The 75-LoC priming-mixin gets folded onto the
:class:`BootstrapPrimingPhase` Command-set class; the mixin
file is deleted.
"""

from __future__ import annotations

from typing import Callable

from media_stack.cli.workflows.controller_job_artifacts_service import (
    ControllerJobArtifacts,
    ControllerJobArtifactsService,
)
from media_stack.cli.workflows.run_controller_job_cli_config_service import (
    RunBootstrapJobConfig,
)
from media_stack.cli.workflows.run_controller_job_orchestration.bootstrap_phase import (
    BootstrapPhase,
)
from media_stack.cli.workflows.run_controller_job_orchestration.config_resolver import (
    BootstrapJobConfigResolver,
)
from media_stack.cli.workflows.run_controller_job_orchestration.hook_dispatcher import (
    BootstrapHookDispatcher,
)
from media_stack.cli.workflows.run_controller_job_orchestration.priming_phase import (
    BootstrapPrimingPhase,
)
from media_stack.cli.workflows.run_controller_job_orchestration.service_factory_bundle import (
    BootstrapJobServiceBundle,
)
from media_stack.core.cli_common import PhaseTracker, info, warn
from media_stack.core.exceptions import ConfigError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


class RunBootstrapJobPipeline:
    """Composition Root + Template Method for the bootstrap-job pipeline."""

    def __init__(
        self,
        cfg: RunBootstrapJobConfig,
        kube: KubernetesClient,
        tracker: PhaseTracker,
    ) -> None:
        self.cfg = cfg
        self.kube = kube
        self.tracker = tracker
        self.artifacts_service = ControllerJobArtifactsService()
        self.artifacts: ControllerJobArtifacts = self.artifacts_service.create()

        # Composition root: wire the dependency graph.
        self.services = BootstrapJobServiceBundle(cfg, self.artifacts, kube)
        self.config_resolver = BootstrapJobConfigResolver(cfg)
        self.priming_phase = BootstrapPrimingPhase(self.services)
        self.hook_dispatcher = BootstrapHookDispatcher(self._hook_context)
        self.bootstrap_phase = BootstrapPhase(
            cfg=cfg,
            artifacts=self.artifacts,
            kube=kube,
            services=self.services,
            config_resolver=self.config_resolver,
            hook_dispatcher=self.hook_dispatcher,
            info_fn=info,
            warn_fn=warn,
        )

    # -- hook context (consumed by BootstrapHookDispatcher) ----------------

    def _hook_context(self) -> dict[str, object]:
        return {
            "namespace": self.cfg.namespace,
            "kube": self.kube,
            "info": info,
            "warn": warn,
            "deployment_exists": self.priming_phase.deployment_exists,
            "restart_deployment": self.priming_phase.restart_deployment,
            "restart_deployment_if_exists": self.priming_phase.restart_deployment_if_exists,
            "read_secret_key": self.priming_phase.read_secret_key,
            "log_contains": self.priming_phase.log_contains,
        }

    # -- run pipeline ------------------------------------------------------

    def run(self) -> int:
        if not self.cfg.config_file.exists():
            raise ConfigError(f"Config file not found: {self.cfg.config_file}")

        info(f"Namespace: {self.cfg.namespace}")
        info(f"Config: {self.cfg.config_file}")
        info(f"Ingress: {self.cfg.ingress_name}")
        info(f"Bootstrap runner image: {self.cfg.bootstrap_runner_image}")
        info(f"Heartbeat interval: {self.cfg.heartbeat_interval}s")
        self.notify(
            "info",
            f"media-stack bootstrap job started (namespace={self.cfg.namespace})",
        )

        try:
            operation_handlers = self._build_operation_handlers()
            for handler_key, spec in self.config_resolver.resolve_call_handler_specs().items():
                hook = self.hook_dispatcher.import_hook(spec)
                operation_handlers[handler_key] = (
                    lambda imported=hook, name=handler_key: self.hook_dispatcher.invoke_hook(
                        imported, hook_name=name,
                    )
                )

            self.services.core_phases_service().run(
                run_phase=self._run_phase,
                run_script=self._run_script,
                operation_handlers=operation_handlers,
            )

            self.services.post_job_actions_service(
                self.config_resolver.resolve_post_job_actions(),
            ).run_actions(
                log_contains=self.priming_phase.log_contains,
                run_phase=self._run_phase,
                restart_deployment=lambda deployment: self.priming_phase.restart_deployment(
                    deployment,
                ),
                restart_deployment_if_exists=lambda deployment: self.priming_phase.restart_deployment_if_exists(
                    deployment,
                ),
            )

            info("Bootstrap job completed.")
            self.tracker.print_summary()
            self.notify(
                "ok",
                f"media-stack bootstrap job completed (namespace={self.cfg.namespace})",
            )
            return 0
        except Exception:
            self.notify(
                "error",
                f"media-stack bootstrap job failed (namespace={self.cfg.namespace})",
            )
            raise
        finally:
            self.cleanup()

    def _build_operation_handlers(self) -> dict[str, Callable[[], None]]:
        return {
            "prepare_bootstrap_job_config": self.bootstrap_phase.prepare_bootstrap_job_config,
            "ensure_bootstrap_pvc_prereqs": self.bootstrap_phase.ensure_bootstrap_pvc_prereqs,
            "prime_servarr_api_keys_secret": self.priming_phase.prime_servarr_api_keys_secret,
            "prime_usenet_client_api_key_secret": self.priming_phase.prime_usenet_client_api_key_secret,
            "prime_request_manager_api_key_secret": self.priming_phase.prime_request_manager_api_key_secret,
            "prime_analytics_api_key_secret": self.priming_phase.prime_analytics_api_key_secret,
            "prime_media_server_api_key_secret": self.priming_phase.prime_media_server_api_key_secret,
            "prime_media_server_user_id_secret": self.priming_phase.prime_media_server_user_id_secret,
            "update_bootstrap_configmaps": self.priming_phase.update_bootstrap_configmaps,
            "ensure_bootstrap_deployment": self.priming_phase.ensure_bootstrap_deployment,
            "wait_for_bootstrap_service": self.bootstrap_phase.wait_for_bootstrap_service,
            "recreate_bootstrap_job": self.priming_phase.recreate_bootstrap_job,
            "wait_for_bootstrap_job": self.priming_phase.wait_for_bootstrap_job,
            "print_bootstrap_job_logs": self.priming_phase.print_bootstrap_job_logs,
        }

    def _run_phase(
        self, phase_name: str, fn: Callable[[], None], *, enabled: bool = True,
    ) -> None:
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
        self.services.notification_service().notify(status, message)

    def _run_script(
        self, script_name: str, *args: str, env: dict[str, str] | None = None,
    ) -> None:
        self.services.script_runner_service().run_script(script_name, *args, env=env)

    # -- test-surface compatibility shims --------------------------------
    # Existing tests address ``runner.manifest_overrides`` /
    # ``runner.prepare_bootstrap_job_config`` / ``runner.prime_*`` directly.
    # Forward each into the matching SRP collaborator so the test
    # surface stays stable across the Phase 7c relocation.

    def manifest_overrides(self, text: str) -> str:
        return self.bootstrap_phase.manifest_overrides(text)

    def ensure_bootstrap_pvc_prereqs(self) -> None:
        self.bootstrap_phase.ensure_bootstrap_pvc_prereqs()

    def prepare_bootstrap_job_config(self) -> None:
        self.bootstrap_phase.prepare_bootstrap_job_config()

    def wait_for_bootstrap_service(self) -> None:
        self.bootstrap_phase.wait_for_bootstrap_service()

    def prime_servarr_api_keys_secret(self) -> None:
        self.priming_phase.prime_servarr_api_keys_secret()

    def prime_usenet_client_api_key_secret(self) -> None:
        self.priming_phase.prime_usenet_client_api_key_secret()

    def prime_request_manager_api_key_secret(self) -> None:
        self.priming_phase.prime_request_manager_api_key_secret()

    def prime_analytics_api_key_secret(self) -> None:
        self.priming_phase.prime_analytics_api_key_secret()

    def prime_media_server_api_key_secret(self) -> None:
        self.priming_phase.prime_media_server_api_key_secret()

    def prime_media_server_user_id_secret(self) -> None:
        self.priming_phase.prime_media_server_user_id_secret()

    def update_bootstrap_configmaps(self) -> None:
        self.priming_phase.update_bootstrap_configmaps()

    def recreate_bootstrap_job(self) -> None:
        self.priming_phase.recreate_bootstrap_job()

    def ensure_bootstrap_deployment(self) -> None:
        self.priming_phase.ensure_bootstrap_deployment()

    def wait_for_bootstrap_job(self) -> None:
        self.priming_phase.wait_for_bootstrap_job()

    def print_bootstrap_job_logs(self) -> None:
        self.priming_phase.print_bootstrap_job_logs()


__all__ = ["RunBootstrapJobPipeline"]
