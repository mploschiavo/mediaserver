"""BootstrapJobServiceBundle — Factory bundle for the bootstrap-job services.

ADR-0015 Phase 7c. Pre-Phase-7c ten parallel factory methods
(``_job_wait_service``, ``_secret_priming_service``,
``_manifest_service``, ``_notification_service``,
``_script_runner_service``, ``_deployment_ops_service``,
``_secret_reader_service``, ``_post_job_actions_service``,
``_core_phases_service``, ``_job_logs_service``) lived on
:class:`RunBootstrapJobRunner` in commands/. Each builds one
workflows-tier service parameterised by the same cfg + artifacts.

This bundle owns those factories so the orchestration code has a
single entry point. Factory pattern (Gang-of-Four): each method
returns a fresh service instance; the construction cost is
negligible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from media_stack.cli.workflows.controller_core_phases_service import (
    ControllerCorePhasesConfig,
    ControllerCorePhasesService,
)
from media_stack.cli.workflows.controller_deployment_ops_service import (
    ControllerDeploymentOpsConfig,
    ControllerDeploymentOpsService,
)
from media_stack.cli.workflows.controller_job_logs_service import (
    ControllerJobLogsConfig,
    ControllerJobLogsService,
)
from media_stack.cli.workflows.controller_job_wait_service import (
    ControllerJobWaitConfig,
    ControllerJobWaitService,
)
from media_stack.cli.workflows.controller_manifest_service import (
    ControllerManifestConfig,
    ControllerManifestService,
)
from media_stack.cli.workflows.controller_notification_service import (
    ControllerNotificationConfig,
    ControllerNotificationService,
)
from media_stack.cli.workflows.controller_post_job_actions_service import (
    ControllerPostJobAction,
    ControllerPostJobActionsService,
)
from media_stack.cli.workflows.controller_secret_priming_service import (
    ControllerSecretPrimingConfig,
    ControllerSecretPrimingService,
)
from media_stack.cli.workflows.controller_secret_reader_service import (
    ControllerSecretReaderConfig,
    ControllerSecretReaderService,
)
from media_stack.cli.workflows.script_runner_service import (
    ScriptRunnerConfig,
    ScriptRunnerService,
)
from media_stack.core.cli_common import info, warn

if TYPE_CHECKING:
    from media_stack.cli.workflows.controller_job_artifacts_service import (
        ControllerJobArtifacts,
    )
    from media_stack.cli.workflows.run_controller_job_cli_config_service import (
        RunBootstrapJobConfig,
    )
    from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


class BootstrapJobServiceBundle:
    """Factory bundle: build the ten workflow services the pipeline uses."""

    def __init__(
        self,
        cfg: "RunBootstrapJobConfig",
        artifacts: "ControllerJobArtifacts",
        kube: "KubernetesClient",
    ) -> None:
        self._cfg = cfg
        self._artifacts = artifacts
        self._kube = kube

    def job_wait_service(self) -> ControllerJobWaitService:
        return ControllerJobWaitService(
            cfg=ControllerJobWaitConfig(
                namespace=self._cfg.namespace,
                timeout_seconds=self._cfg.timeout_seconds,
                timeout_raw=self._cfg.timeout_raw,
                heartbeat_interval=self._cfg.heartbeat_interval,
            ),
            kube=self._kube,
            info=info,
            warn=warn,
        )

    def secret_priming_service(self) -> ControllerSecretPrimingService:
        return ControllerSecretPrimingService(
            cfg=ControllerSecretPrimingConfig(
                namespace=self._cfg.namespace,
                bootstrap_config_file=self._artifacts.job_config_file,
            ),
            kube=self._kube,
            info=info,
            warn=warn,
        )

    def manifest_service(self) -> ControllerManifestService:
        return ControllerManifestService(
            cfg=ControllerManifestConfig(
                namespace=self._cfg.namespace,
                root_dir=self._cfg.root_dir,
                prepare_host_root=self._cfg.prepare_host_root,
                bootstrap_runner_image=self._cfg.bootstrap_runner_image,
                job_config_file=self._artifacts.job_config_file,
                bootstrap_profile_file=self._cfg.bootstrap_profile_file,
            ),
            kube=self._kube,
            info=info,
            warn=warn,
        )

    def notification_service(self) -> ControllerNotificationService:
        return ControllerNotificationService(
            cfg=ControllerNotificationConfig(
                alert_webhook_url=self._cfg.alert_webhook_url,
            )
        )

    def script_runner_service(self) -> ScriptRunnerService:
        return ScriptRunnerService(
            cfg=ScriptRunnerConfig(root_dir=self._cfg.root_dir),
        )

    def deployment_ops_service(self) -> ControllerDeploymentOpsService:
        return ControllerDeploymentOpsService(
            cfg=ControllerDeploymentOpsConfig(namespace=self._cfg.namespace),
            kube=self._kube,
            info=info,
        )

    def secret_reader_service(self) -> ControllerSecretReaderService:
        return ControllerSecretReaderService(
            cfg=ControllerSecretReaderConfig(namespace=self._cfg.namespace),
            kube=self._kube,
        )

    def post_job_actions_service(
        self, actions: list[ControllerPostJobAction],
    ) -> ControllerPostJobActionsService:
        return ControllerPostJobActionsService(actions=actions)

    def core_phases_service(self) -> ControllerCorePhasesService:
        return ControllerCorePhasesService(
            ControllerCorePhasesConfig(
                config_file=self._cfg.config_file,
                namespace=self._cfg.namespace,
                prepare_host_root=self._cfg.prepare_host_root,
                phase_skip_flags=self._cfg.effective_phase_skip_flags,
            )
        )

    def job_logs_service(self) -> ControllerJobLogsService:
        return ControllerJobLogsService(
            cfg=ControllerJobLogsConfig(
                namespace=self._cfg.namespace,
                job_name="media-stack-controller",
                log_file=self._artifacts.job_log_file,
                tail_lines=self._cfg.job_log_tail_lines,
            ),
            kube=self._kube,
        )


__all__ = ["BootstrapJobServiceBundle"]
