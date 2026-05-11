"""DeployServiceFactoryBundle — Factory bundle for workflow service deps.

ADR-0015 Phase 4. Pre-Phase-4 four parallel ``_notification_service``,
``_script_runner_service``, ``_profile_defaults_service``,
``_pipeline_service`` factories lived on ``RunnerServicesMixin`` (a
god-mixin in commands/). Each builds one workflows-tier service
parameterised by the same cfg + config-service surface.

This bundle owns those four factories so the orchestration code
has a single entry point for "give me the workflow service that
does X". The factories return new instances on each call —
:class:`DeployPipelineService` keeps no internal state worth
sharing across phases, and the construction cost is negligible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from media_stack.cli.workflows.controller_notification_service import (
    ControllerNotificationConfig,
    ControllerNotificationService,
)
from media_stack.cli.workflows.deploy_pipeline_service import (
    DeployPipelineConfig,
    DeployPipelineService,
)
from media_stack.cli.workflows.deploy_profile_defaults_service import (
    DeployProfileDefaultsService,
)
from media_stack.cli.workflows.script_runner_service import (
    ScriptRunnerConfig,
    ScriptRunnerService,
)
from media_stack.core.cli_common import info

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )
    from media_stack.cli.workflows.deploy_config import DeployConfigService
    from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
        DeployRuntimeOptions,
    )


class DeployServiceFactoryBundle:
    """Factory bundle: build the four workflow services the pipeline uses.

    Constructor injection wires cfg + config-service + runtime-
    options so each factory has the deps it needs without
    accessing the runner. ``run_script_callback`` is injected at
    construction (instead of resolved from the runner per call)
    because :class:`DeployPipelineService` takes it as an
    initialisation arg and the pipeline lifecycle outlives any
    one phase.
    """

    def __init__(
        self,
        cfg: "DeployStackConfig",
        runtime_options: "DeployRuntimeOptions",
        config_service: "DeployConfigService",
        run_script_callback: Callable[..., None],
    ) -> None:
        self._cfg = cfg
        self._runtime_options = runtime_options
        self._config_service = config_service
        self._run_script_callback = run_script_callback

    def notification_service(self) -> ControllerNotificationService:
        return ControllerNotificationService(
            cfg=ControllerNotificationConfig(
                alert_webhook_url=self._cfg.alert_webhook_url,
            )
        )

    def script_runner_service(self) -> ScriptRunnerService:
        return ScriptRunnerService(
            cfg=ScriptRunnerConfig(
                root_dir=self._cfg.root_dir,
                extra_env={"NAMESPACE": self._cfg.namespace},
            )
        )

    def profile_defaults_service(self) -> DeployProfileDefaultsService:
        return DeployProfileDefaultsService()

    def pipeline_service(self) -> DeployPipelineService:
        return DeployPipelineService(
            cfg=DeployPipelineConfig(
                namespace=self._cfg.namespace,
                root_dir=self._cfg.root_dir,
                prepare_host_root=self._cfg.prepare_host_root,
                enable_components=self._cfg.enable_components,
                selected_apps=self._cfg.selected_apps,
                internet_exposed=self._cfg.internet_exposed,
                route_strategy=self._cfg.route_strategy,
                ingress_domain=self._cfg.ingress_domain,
                app_gateway_host=self._cfg.app_gateway_host,
                app_gateway_port=self._cfg.app_gateway_port,
                app_path_prefix=self._cfg.app_path_prefix,
                media_server_direct_host=self._cfg.media_server_direct_host,
                auth_provider=self._cfg.auth_provider,
                auth_middleware=self._cfg.auth_middleware,
                edge_router_provider=self._config_service.edge_router_provider(),
                preconfigure_api_keys=self._cfg.preconfigure_api_keys,
                apply_initial_preferences=self._cfg.apply_initial_preferences,
                auto_download_content=self._cfg.auto_download_content,
                config_file=self._cfg.config_file,
                platform_target=self._runtime_options.resolved_platform_target(),
                bootstrap_profile_file=str(self._cfg.bootstrap_profile_file or ""),
            ),
            info=info,
            run_script=self._run_script_callback,
        )


__all__ = ["DeployServiceFactoryBundle"]
