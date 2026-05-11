"""BootstrapPhase — Command set for prepare-config + PVC prereqs + service wait.

ADR-0015 Phase 7c. Pre-Phase-7c these phase actions
(``prepare_bootstrap_job_config``, ``ensure_bootstrap_pvc_prereqs``,
``manifest_overrides``, ``wait_for_bootstrap_service``) sat as
methods on :class:`RunBootstrapJobRunner` alongside everything
else. They share the responsibility of preparing the bootstrap
service for execution + triggering it; Phase 7c groups them onto
their own Command-set class.

The runtime-config-policy hook is dispatched from inside
``prepare_bootstrap_job_config`` via the injected
:class:`BootstrapHookDispatcher`.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Callable

from media_stack.core.exceptions import ConfigError
from media_stack.services.top_level_config_model import TopLevelBootstrapConfig

if TYPE_CHECKING:
    from media_stack.cli.workflows.controller_job_artifacts_service import (
        ControllerJobArtifacts,
    )
    from media_stack.cli.workflows.run_controller_job_cli_config_service import (
        RunBootstrapJobConfig,
    )
    from media_stack.cli.workflows.run_controller_job_orchestration.config_resolver import (
        BootstrapJobConfigResolver,
    )
    from media_stack.cli.workflows.run_controller_job_orchestration.hook_dispatcher import (
        BootstrapHookDispatcher,
    )
    from media_stack.cli.workflows.run_controller_job_orchestration.service_factory_bundle import (
        BootstrapJobServiceBundle,
    )
    from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


_BOOTSTRAP_SELECTOR = "app=media-stack-controller"
_BOOTSTRAP_POD_DEADLINE_SECONDS = 120
_BOOTSTRAP_POD_POLL_INTERVAL_SECONDS = 3
_BOOTSTRAP_TRIGGER_TIMEOUT_SECONDS = 10


class BootstrapPhase:
    """Command set: prepare config + PVC prereqs + manifest overrides + service wait."""

    def __init__(
        self,
        cfg: "RunBootstrapJobConfig",
        artifacts: "ControllerJobArtifacts",
        kube: "KubernetesClient",
        services: "BootstrapJobServiceBundle",
        config_resolver: "BootstrapJobConfigResolver",
        hook_dispatcher: "BootstrapHookDispatcher",
        info_fn: Callable[[str], None],
        warn_fn: Callable[[str], None],
    ) -> None:
        self._cfg = cfg
        self._artifacts = artifacts
        self._kube = kube
        self._services = services
        self._config_resolver = config_resolver
        self._hook_dispatcher = hook_dispatcher
        self._info = info_fn
        self._warn = warn_fn

    def manifest_overrides(self, text: str) -> str:
        return self._services.manifest_service().manifest_overrides(text)

    def ensure_bootstrap_pvc_prereqs(self) -> None:
        self._services.manifest_service().ensure_bootstrap_pvc_prereqs()

    def prepare_bootstrap_job_config(self) -> None:
        payload = json.loads(self._cfg.config_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ConfigError(f"Expected JSON object in {self._cfg.config_file}")
        try:
            cfg = TopLevelBootstrapConfig.from_dict(payload).to_dict()
        except ValueError as exc:
            raise ConfigError(
                f"Invalid bootstrap config at {self._cfg.config_file}: {exc}"
            ) from exc
        self._info(
            f"Config policy inputs: route_strategy={self._cfg.route_strategy}, "
            f"gateway_host={self._cfg.app_gateway_host}, "
            f"path_prefix={self._cfg.app_path_prefix}, "
            f"ingress_domain={self._cfg.ingress_domain}, "
            f"media_server_direct={self._cfg.media_server_direct_host}"
        )
        self._apply_runtime_config_policy(cfg)
        self._artifacts.job_config_file.write_text(
            json.dumps(cfg, indent=2) + "\n",
            encoding="utf-8",
        )
        from media_stack.services.apps.stack.config_diagnostics import (
            log_config_policy_values,
        )
        log_config_policy_values(cfg, self._info)
        self._info(
            "Bootstrap preconfigure flags: "
            f"api_keys={'on' if self._cfg.preconfigure_api_keys else 'off'}, "
            f"initial_preferences={'on' if self._cfg.apply_initial_preferences else 'off'}"
        )
        self._info(
            "Bootstrap content mode: "
            f"{'automatic download enabled' if self._cfg.auto_download_content else 'manual download mode'}"
        )
        self._info(f"Prepared bootstrap job config: {self._artifacts.job_config_file}")

    def _apply_runtime_config_policy(self, cfg: dict[str, object]) -> None:
        spec = self._config_resolver.runtime_config_policy_handler_spec()
        if not spec:
            raise ConfigError(
                "adapter_hooks.bootstrap_job.runtime_config_policy_handler must be set"
            )
        hook = self._hook_dispatcher.import_hook(spec)
        self._hook_dispatcher.invoke_hook_with_context(
            hook,
            hook_name="apply_runtime_config_policy",
            context={
                "cfg": cfg,
                "selected_apps_csv": self._cfg.selected_apps,
                "auto_download_content": self._cfg.auto_download_content,
                "internet_exposed": self._cfg.internet_exposed,
                "route_strategy": self._cfg.route_strategy,
                "ingress_domain": self._cfg.ingress_domain,
                "app_gateway_host": self._cfg.app_gateway_host,
                "app_path_prefix": self._cfg.app_path_prefix,
                "media_server_direct_host": self._cfg.media_server_direct_host,
            },
        )

    def wait_for_bootstrap_service(self) -> None:
        wait_svc = self._services.job_wait_service()
        port = wait_svc.cfg.service_port

        self._info("Waiting for bootstrap service pod...")
        pod_name = None
        deadline = time.time() + _BOOTSTRAP_POD_DEADLINE_SECONDS
        while time.time() < deadline:
            pod_name = wait_svc._find_bootstrap_pod(selector=_BOOTSTRAP_SELECTOR)
            if pod_name:
                status = wait_svc._query_bootstrap_status(pod_name)
                if status is not None:
                    self._info(f"Bootstrap service responding on pod {pod_name}")
                    break
                pod_name = None
            time.sleep(_BOOTSTRAP_POD_POLL_INTERVAL_SECONDS)

        if not pod_name:
            raise ConfigError(
                f"Bootstrap service pod not found or not responding within "
                f"{_BOOTSTRAP_POD_DEADLINE_SECONDS}s"
            )

        self._info(f"Triggering bootstrap action on pod {pod_name}")
        trigger_script = (
            "import urllib.request,json; "
            "req=urllib.request.Request("
            f"'http://127.0.0.1:{port}/actions/bootstrap',"
            "data=b'{}',"
            "headers={'Content-Type':'application/json'}); "
            f"r=urllib.request.urlopen(req,timeout={_BOOTSTRAP_TRIGGER_TIMEOUT_SECONDS}); "
            "print(r.read().decode())"
        )
        result = self._kube.run(
            ["-n", self._cfg.namespace, "exec", pod_name, "--",
             "python3", "-c", trigger_script],
            check=False,
        )
        if result.stdout:
            self._info(f"Bootstrap trigger response: {result.stdout.strip()}")
        if result.returncode != 0:
            self._warn(
                f"Bootstrap trigger may have failed: {result.stderr or 'unknown error'}"
            )

        wait_svc.wait_for_bootstrap_service(selector=_BOOTSTRAP_SELECTOR)


__all__ = ["BootstrapPhase"]
