"""Bootstrap orchestration runner extracted from bootstrap-apps entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .apps.servarr.pipeline_service import ServarrPipelineInputs
from .apps.servarr.types import ClientAuth, ServarrRunConfig
from .download_client_pipeline_service import (
    DownloadClientPipelineInputs,
    DownloadClientPipelineResult,
    DownloadClientPipelineService,
)
from .enums import BootstrapMode, RunnerEvent
from .media_server_adapters import MediaServerAdapterContext, MediaServerAdapterFactory
from .runner_operations_service import RunnerOperationRegistry
from .runner_phase_plan_service import run_phase_plan as run_runner_phase_plan
from .runtime_models import BootstrapRuntime
from .runtime_service_registry import (
    get_runtime_context_cfg,
    resolve_app_service_class,
    set_runtime_context_cfg,
)

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
StepActionFn = Callable[[], None]


@dataclass
class BootstrapRunnerDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    operations: RunnerOperationRegistry


@dataclass
class BootstrapRunnerService:
    deps: BootstrapRunnerDependencies
    _download_client_prepare_result: DownloadClientPipelineResult | None = None

    def _invoke_handler(
        self,
        event: RunnerEvent | str,
        handler: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return self.deps.operations.invoke_event(event, handler, *args, **kwargs)

    def _technology_aliases(self, rt: BootstrapRuntime) -> dict[str, str]:
        aliases: dict[str, str] = {}
        raw = (rt.adapter_hooks_cfg or {}).get("technology_aliases") or {}
        if isinstance(raw, dict):
            for source, target in raw.items():
                src = str(source or "").strip().lower()
                dst = str(target or "").strip().lower()
                if not src or not dst:
                    continue
                aliases[src] = dst
        return aliases

    def _canonical_tech_key(self, raw: str, rt: BootstrapRuntime) -> str:
        token = str(raw or "").strip().lower()
        if not token:
            return ""
        return self._technology_aliases(rt).get(token, token)

    def _runner_operation_plans(self, rt: BootstrapRuntime) -> dict[str, Any]:
        hooks = rt.adapter_hooks_cfg or {}
        plans = hooks.get("runner_event_plans") or hooks.get("runner_operation_plans") or {}
        return plans if isinstance(plans, dict) else {}

    def _run_runner_plan_phase(self, rt: BootstrapRuntime, phase_name: str) -> bool:
        return run_runner_phase_plan(
            runtime=rt,
            plan_cfg=self._runner_operation_plans(rt),
            phase_name=phase_name,
            invoke_event=self._invoke_handler,
            run_optional_step=self._run_optional_step,
            log=self.deps.log,
        )

    def _wait_for_servarr_services(self, rt: BootstrapRuntime) -> None:
        for app in rt.arr_apps:
            self.deps.wait_for_service(app.name, app.url, "/ping", rt.wait_timeout)

    def _download_client_pipeline_service(
        self, rt: BootstrapRuntime
    ) -> DownloadClientPipelineService:
        service_cls = resolve_app_service_class(
            "download_client_pipeline_service",
            DownloadClientPipelineService,
        )
        return service_cls(
            log=self.deps.log,
            normalize_url=self.deps.normalize_url,
            wait_for_service=self.deps.wait_for_service,
            bool_cfg=self.deps.bool_cfg,
            invoke_handler=self._invoke_handler,
        )

    def _prepare_download_clients(self, rt: BootstrapRuntime) -> DownloadClientPipelineResult:
        if self._download_client_prepare_result is not None:
            return self._download_client_prepare_result
        self._download_client_prepare_result = self._download_client_pipeline_service(
            rt
        ).run_prepare(
            DownloadClientPipelineInputs(
                config_root=rt.config_root,
                arr_apps_raw=rt.arr_apps_raw,
                qbit_cfg=rt.qbit_cfg,
                qbit_username=rt.qb_user,
                qbit_password=rt.qb_pass,
                qbit_login_required=rt.qbit_login_required,
                configure_qbit_arr_clients=rt.configure_qbit_arr_clients,
                set_qbit_categories=rt.set_qbit_categories,
                sab_cfg=rt.sab_cfg,
                configure_sab_arr_clients=rt.configure_sab_arr_clients,
                fully_preconfigured=rt.fully_preconfigured,
                wait_timeout=rt.wait_timeout,
                adapter_hooks_cfg=rt.adapter_hooks_cfg,
                torrent_client_key=rt.torrent_client_key,
                usenet_client_key=rt.usenet_client_key,
            )
        )
        return self._download_client_prepare_result

    def _media_server_adapter(self, rt: BootstrapRuntime) -> Any:
        factory_cls = resolve_app_service_class(
            "media_server_adapter_factory",
            MediaServerAdapterFactory,
        )
        factory = factory_cls(
            adapter_class_specs=(rt.adapter_hooks_cfg or {}).get("media_server_adapter_classes"),
        )
        backend = self._canonical_tech_key(str(rt.media_server_backend or ""), rt)
        if not backend:
            raise RuntimeError(
                "Missing media_server backend binding. "
                "Set technology_bindings.media_server and provide a matching "
                "media_server adapter in plugin manifests."
            )
        return factory.create(
            backend,
            MediaServerAdapterContext(
                backend=backend,
                runtime=rt,
                invoke_event=self._invoke_handler,
                run_optional_step=self._run_optional_step,
                log=self.deps.log,
            ),
        )

    def _run_media_server_prewarm_mode(self, rt: BootstrapRuntime) -> None:
        self._media_server_adapter(rt).run_prewarm_mode()

    def _run_media_server_home_rails_mode(self, rt: BootstrapRuntime) -> None:
        self._media_server_adapter(rt).run_home_rails_mode()

    def _run_media_hygiene_mode(self, rt: BootstrapRuntime) -> None:
        self._run_runner_plan_phase(rt, "precheck_steps")
        for app in rt.arr_apps:
            try:
                self.deps.wait_for_service(app.name, app.url, "/ping", rt.wait_timeout)
            except Exception as exc:
                self.deps.log(
                    f"[WARN] Media hygiene mode: service wait skipped for {app.name} ({exc})"
                )
        self._invoke_handler(
            RunnerEvent.RUN,
            "run_media_hygiene",
            rt.cfg,
            rt.config_root,
            rt.arr_apps_raw,
            rt.app_keys,
            rt.qbit_cfg,
            rt.qb_user,
            rt.qb_pass,
        )
        self.deps.log("[OK] Media hygiene mode complete.")

    def _run_mode_shortcuts(self, rt: BootstrapRuntime) -> bool:
        handlers: dict[BootstrapMode, Callable[[BootstrapRuntime], None]] = {
            BootstrapMode.MEDIA_SERVER_PREWARM: self._run_media_server_prewarm_mode,
            BootstrapMode.MEDIA_SERVER_HOME_RAILS: self._run_media_server_home_rails_mode,
            BootstrapMode.MEDIA_HYGIENE: self._run_media_hygiene_mode,
        }
        handler = handlers.get(rt.mode)
        if not handler:
            return False
        handler(rt)
        return True

    def _run_optional_step(
        self,
        *,
        enabled: bool,
        required: bool,
        action: StepActionFn,
        warning_message: str,
    ) -> None:
        if not enabled:
            return
        try:
            action()
        except Exception as exc:
            if required:
                raise
            self.deps.log(f"{warning_message} ({exc})")

    def _run_full_prechecks(self, rt: BootstrapRuntime) -> tuple[bool, str]:
        self._run_runner_plan_phase(rt, "precheck_steps")
        self._wait_for_servarr_services(rt)
        pipeline_prepare = self._prepare_download_clients(rt)
        return pipeline_prepare.qbit_login_ok, pipeline_prepare.sab_api_key

    def _run_servarr_pipeline(
        self, rt: BootstrapRuntime, qbit_login_ok: bool, sab_api_key: str
    ) -> None:
        self._invoke_handler(
            RunnerEvent.RUN,
            "run_servarr_pipeline",
            ServarrPipelineInputs(
                cfg=rt.cfg,
                arr_apps=rt.arr_apps,
                app_keys=rt.app_keys,
                prowlarr_url=rt.prowlarr_url,
                prowlarr_key=rt.prowlarr_key,
                app_auth_cfg=rt.app_auth_cfg,
                arr_media_management_cfg=rt.arr_media_management_cfg,
                arr_download_handling_cfg=rt.arr_download_handling_cfg,
                arr_quality_upgrade_cfg=rt.arr_quality_upgrade_cfg,
                qbit_cfg=rt.qbit_cfg,
                qbit_auth=ClientAuth(username=rt.qb_user, password=rt.qb_pass),
                sab_cfg=rt.sab_cfg,
                sab_auth=ClientAuth(username=rt.sab_username, password=rt.sab_password),
                sab_remote_path_mappings=rt.sab_remote_path_mappings,
                adapter_hooks_cfg=rt.adapter_hooks_cfg,
                run_cfg=ServarrRunConfig(
                    configure_arr_media_management=rt.configure_arr_media_management,
                    configure_arr_download_handling=rt.configure_arr_download_handling,
                    configure_arr_quality_upgrade=rt.configure_arr_quality_upgrade,
                    configure_arr_discovery_lists=rt.configure_arr_discovery_lists,
                    configure_qbit_arr_clients=rt.configure_qbit_arr_clients,
                    qbit_login_ok=qbit_login_ok,
                    configure_sab_arr_clients=rt.configure_sab_arr_clients,
                    sab_api_key=sab_api_key,
                    refresh_health_after_bootstrap=rt.refresh_health_after_bootstrap,
                ),
            ),
        )

    def _run_post_servarr_steps(self, rt: BootstrapRuntime) -> None:
        self._run_runner_plan_phase(rt, "post_servarr_pre_media_steps")
        self._media_server_adapter(rt).run_post_servarr_pre_hygiene_steps()
        self._run_runner_plan_phase(rt, "post_servarr_post_media_steps")
        self._media_server_adapter(rt).run_post_servarr_post_hygiene_steps()

    def _run_indexers(self, rt: BootstrapRuntime) -> None:
        self._run_runner_plan_phase(rt, "indexer_steps")

    def run(self, rt: BootstrapRuntime) -> None:
        prior_hooks = get_runtime_context_cfg()
        runtime_context_cfg = dict(rt.adapter_hooks_cfg or {})
        runtime_bindings = rt.cfg.get("technology_bindings") if isinstance(rt.cfg, dict) else {}
        runtime_context_cfg["runtime_bindings"] = (
            dict(runtime_bindings) if isinstance(runtime_bindings, dict) else {}
        )
        set_runtime_context_cfg(runtime_context_cfg)
        try:
            self._download_client_prepare_result = None

            if self._run_mode_shortcuts(rt):
                return

            qbit_login_ok, sab_api_key = self._run_full_prechecks(rt)
            self._run_servarr_pipeline(rt, qbit_login_ok=qbit_login_ok, sab_api_key=sab_api_key)
            self._run_post_servarr_steps(rt)
            self._run_indexers(rt)
            self.deps.log("[OK] Bootstrap complete.")
        finally:
            set_runtime_context_cfg(prior_hooks)
