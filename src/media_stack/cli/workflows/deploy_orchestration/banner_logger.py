"""DeployBannerLogger — Builder for the operator-facing run-start banner.

ADR-0015 Phase 4. The pre-Phase-4 ``RunnerPhasesMixin.run()`` inlined
a ~50-line banner-print block right after the profile-defaults
phase. Pulling it onto its own class drops two ratchet counts
(``METHODS_OVER_50_LINES`` on ``run()``,
``FILES_OVER_400_LINES`` on ``deploy_pipeline.py``) and groups the
banner-state-derivation responsibility (cfg → human-readable
lines) under one named class.

Builder pattern: ``log()`` walks the cfg + platform_plugin and
emits one ``info()``/``warn()`` per line. No state; constructor-
injected ``runtime_options`` supplies the delete-env safeguard
+ chaos action lookups.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.core.cli_common import info, warn

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )
    from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
        DeployRuntimeOptions,
    )
    from media_stack.core.platform_plugin_contract import PlatformPlugin


class DeployBannerLogger:
    """Builder: emit the operator-facing run-start banner to ``info``/``warn``."""

    def __init__(
        self,
        cfg: "DeployStackConfig",
        runtime_options: "DeployRuntimeOptions",
    ) -> None:
        self._cfg = cfg
        self._runtime_options = runtime_options

    def log(self, target: str, platform_plugin: "PlatformPlugin") -> None:
        cfg = self._cfg
        info(f"Namespace: {cfg.namespace}")
        info(f"Profile: {cfg.profile}")
        info(f"Platform target: {target}")
        info(f"Purpose: {cfg.purpose}")
        info(f"Disk allocation (GB): {cfg.disk_allocation_gb}")
        info(f"Network CIDR: {cfg.network_cidr}")
        info(f"Ingress domain: {cfg.ingress_domain}")
        info(f"Config: {cfg.config_file}")
        self._log_delete_namespace_state()
        info(f"Storage mode: {cfg.storage_mode}")
        if cfg.pvc_storage_class:
            info(f"PVC storage class override: {cfg.pvc_storage_class}")
        else:
            info("PVC storage class override: <cluster default>")
        info(f"Include optional: {cfg.include_optional}")
        info(f"Enable components: {cfg.enable_components}")
        info(f"Run bootstrap: {cfg.run_bootstrap}")
        info(f"Preconfigure API keys: {cfg.preconfigure_api_keys}")
        info(f"Apply initial preferences: {cfg.apply_initial_preferences}")
        info(f"Auto-download content: {cfg.auto_download_content}")
        info(f"Generate secrets on rebuild: {cfg.generate_secrets_on_rebuild}")
        info(f"Preserve secret on rebuild: {cfg.preserve_secret_on_rebuild}")
        info(f"Selected apps: {cfg.selected_apps or '<all>'}")
        info(
            "Exposure: "
            f"internet={cfg.internet_exposed}, "
            f"route_strategy={cfg.route_strategy}, "
            f"auth_provider={cfg.auth_provider}"
        )
        if cfg.app_gateway_host:
            info(f"App gateway host: {cfg.app_gateway_host}")
        if cfg.app_gateway_port:
            info(f"App gateway port: {cfg.app_gateway_port}")
        if cfg.media_server_direct_host:
            info(f"Media-server direct host: {cfg.media_server_direct_host}")
        if platform_plugin.logs_bootstrap_runner_image:
            info(f"Compose controller image: {cfg.bootstrap_runner_image}")
        info(
            "Chaos testing: "
            f"enabled={cfg.chaos_enabled}, "
            f"duration_minutes={cfg.chaos_duration_minutes}, "
            f"interval_seconds={cfg.chaos_interval_seconds}, "
            f"actions={','.join(self._runtime_options.chaos_actions()) or '<none>'}"
        )

    def _log_delete_namespace_state(self) -> None:
        delete_requested = self._runtime_options.delete_environment_requested()
        delete_enabled = self._runtime_options.delete_environment_enabled()
        if delete_enabled:
            warn(
                "Delete namespace: ENABLED — existing environment will be fully torn down "
                "(DELETE_NAMESPACE=1 + DELETE_NAMESPACE_CONFIRM). Set DELETE_NAMESPACE=0 to skip teardown."
            )
        elif delete_requested:
            warn("Delete namespace: requested but blocked by safety confirmation safeguard.")
        else:
            info("Delete namespace: disabled (set DELETE_NAMESPACE=1 to enable full teardown)")


__all__ = ["DeployBannerLogger"]
