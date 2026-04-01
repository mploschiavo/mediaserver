"""Bootstrap orchestration runner extracted from bootstrap-apps entrypoint."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from .download_client_pipeline_service import (
    DownloadClientPipelineInputs,
    DownloadClientPipelineResult,
    DownloadClientPipelineService,
)
from .enums import BootstrapMode, RunnerOperation
from .media_server_adapters import MediaServerAdapterContext, MediaServerAdapterFactory
from .runner_operations_service import RunnerOperationRegistry
from .runner_phase_plan_service import run_phase_plan as run_runner_phase_plan
from .runtime_models import BootstrapRuntime
from .servarr_pipeline_service import ServarrPipelineInputs
from .servarr_types import ClientAuth, ServarrRunConfig
from .technology_lifecycle_service import (
    TechnologyLifecycle,
    TechnologyLifecycleManager,
)

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
StepActionFn = Callable[[], None]


def _resolve_service_class(cfg: dict[str, Any], service_key: str, default_cls: type[Any]) -> type[Any]:
    key = str(service_key or "").strip()
    if not key:
        return default_cls
    hooks = (cfg.get("adapter_hooks") or {}) if isinstance(cfg, dict) else {}
    if not isinstance(hooks, dict):
        raise RuntimeError("adapter_hooks must be an object/map.")
    service_map = hooks.get("app_service_classes") or {}
    if not isinstance(service_map, dict):
        raise RuntimeError("adapter_hooks.app_service_classes must be an object/map.")
    raw_spec = service_map.get(key)
    if raw_spec is None or str(raw_spec).strip() == "":
        return default_cls

    spec = str(raw_spec).strip()
    if ":" not in spec:
        raise RuntimeError(
            f"adapter_hooks.app_service_classes.{key}: invalid class spec '{spec}' "
            "(expected 'module.submodule:ClassName')."
        )
    module_name, class_name = spec.rsplit(":", 1)
    module_name = module_name.strip()
    class_name = class_name.strip()
    if not module_name or not class_name:
        raise RuntimeError(
            f"adapter_hooks.app_service_classes.{key}: invalid class spec '{spec}' "
            "(expected 'module.submodule:ClassName')."
        )
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name, None)
    if not inspect.isclass(cls):
        raise RuntimeError(
            f"adapter_hooks.app_service_classes.{key}: '{spec}' does not resolve to a class."
        )
    return cls


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
    lifecycle_manager: TechnologyLifecycleManager | None = None
    _download_client_prepare_result: DownloadClientPipelineResult | None = None

    def _invoke_operation(self, operation: RunnerOperation | str, *args: Any, **kwargs: Any) -> Any:
        return self.deps.operations.invoke(operation, *args, **kwargs)

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

    def _technology_keys_from_hook_map(self, rt: BootstrapRuntime, hook_key: str) -> tuple[str, ...]:
        hook_map = (rt.adapter_hooks_cfg or {}).get(hook_key) or {}
        if not isinstance(hook_map, dict):
            return ()
        keys = [self._canonical_tech_key(str(key), rt) for key in hook_map.keys()]
        return tuple(dict.fromkeys([key for key in keys if key]))

    def _app_service_lifecycle_keys(self, rt: BootstrapRuntime) -> tuple[str, ...]:
        overrides_raw = (rt.adapter_hooks_cfg or {}).get("service_technology_map") or {}
        overrides = (
            {
                str(key or "").strip().lower(): self._canonical_tech_key(str(value or ""), rt)
                for key, value in overrides_raw.items()
                if str(key or "").strip()
            }
            if isinstance(overrides_raw, dict)
            else {}
        )
        hook_map = (rt.adapter_hooks_cfg or {}).get("app_service_classes") or {}
        if not isinstance(hook_map, dict):
            return ()
        keys: list[str] = []
        for service_key in hook_map.keys():
            key = str(service_key or "").strip().lower()
            if not key:
                continue
            tech_key = overrides.get(key, "")
            if not tech_key:
                if key.endswith("_service"):
                    stem = key[: -len("_service")]
                else:
                    stem = key
                tech_key = stem.split("_", 1)[0]
            if not tech_key:
                continue
            canonical = self._canonical_tech_key(tech_key, rt)
            if canonical:
                keys.append(canonical)
        return tuple(dict.fromkeys(keys))

    def _baseline_lifecycle_keys(self, rt: BootstrapRuntime) -> tuple[str, ...]:
        keys = list(self._technology_keys_from_hook_map(rt, "adapter_classes"))
        keys += list(self._technology_keys_from_hook_map(rt, "download_client_adapter_classes"))
        keys += list(self._technology_keys_from_hook_map(rt, "media_server_adapter_classes"))
        keys += list(self._app_service_lifecycle_keys(rt))
        backend = self._canonical_tech_key(str(rt.media_server_backend or ""), rt)
        if backend:
            keys.append(backend)
        return tuple(dict.fromkeys([key for key in keys if key]))

    def _arr_lifecycle_keys(self, rt: BootstrapRuntime) -> tuple[str, ...]:
        keys = [
            self._canonical_tech_key(app.implementation, rt)
            for app in rt.arr_apps
            if str(app.implementation or "").strip()
        ]
        if not keys:
            keys = list(self._technology_keys_from_hook_map(rt, "adapter_classes"))
        return tuple(dict.fromkeys(keys))

    def _download_client_lifecycle_keys(self, rt: BootstrapRuntime) -> tuple[str, ...]:
        keys = [
            self._canonical_tech_key(rt.torrent_client_key, rt),
            self._canonical_tech_key(rt.usenet_client_key, rt),
        ]
        return tuple(dict.fromkeys([k for k in keys if k]))

    def _non_servarr_aux_lifecycle_keys(self, rt: BootstrapRuntime) -> tuple[str, ...]:
        arr_keys = set(self._arr_lifecycle_keys(rt))
        download_keys = set(self._download_client_lifecycle_keys(rt))
        keys = [
            key
            for key in self._baseline_lifecycle_keys(rt)
            if key not in arr_keys and key not in download_keys
        ]
        return tuple(dict.fromkeys(keys))

    def _build_lifecycle_manager(self, rt: BootstrapRuntime) -> TechnologyLifecycleManager:
        all_keys = list(
            dict.fromkeys(
                list(self._baseline_lifecycle_keys(rt))
                + list(self._arr_lifecycle_keys(rt))
                + list(self._download_client_lifecycle_keys(rt))
            )
        )
        lifecycles = {key: TechnologyLifecycle(key=key) for key in all_keys}
        torrent_key = self._canonical_tech_key(rt.torrent_client_key, rt)
        usenet_key = self._canonical_tech_key(rt.usenet_client_key, rt)
        if torrent_key in lifecycles:
            lifecycles[torrent_key].prepare_fn = lambda runtime, state: state.details.update(
                {"login_ok": self._prepare_download_clients(runtime).qbit_login_ok}
            )
        if usenet_key in lifecycles:
            lifecycles[usenet_key].prepare_fn = lambda runtime, state: state.details.update(
                {"api_key": self._prepare_download_clients(runtime).sab_api_key}
            )
        if "media_hygiene" in lifecycles:
            lifecycles["media_hygiene"].clean_hygiene_fn = (
                lambda runtime, state: state.details.update({"ran": True})
            )

        manager_cls = _resolve_service_class(
            rt.cfg,
            "technology_lifecycle_manager",
            TechnologyLifecycleManager,
        )
        return manager_cls(lifecycles=lifecycles)

    def _run_lifecycle_phase(
        self,
        phase: str,
        rt: BootstrapRuntime,
        keys: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        if not self.lifecycle_manager:
            return
        self.lifecycle_manager.run_phase(phase, rt, keys=keys)

    def _runner_operation_plans(self, rt: BootstrapRuntime) -> dict[str, Any]:
        plans = (rt.adapter_hooks_cfg or {}).get("runner_operation_plans") or {}
        return plans if isinstance(plans, dict) else {}

    def _run_runner_plan_phase(self, rt: BootstrapRuntime, phase_name: str) -> bool:
        return run_runner_phase_plan(
            runtime=rt,
            plan_cfg=self._runner_operation_plans(rt),
            phase_name=phase_name,
            invoke_operation=self._invoke_operation,
            run_optional_step=self._run_optional_step,
            log=self.deps.log,
        )

    def _wait_for_servarr_services(self, rt: BootstrapRuntime) -> None:
        for app in rt.arr_apps:
            self.deps.wait_for_service(app.name, app.url, "/ping", rt.wait_timeout)

    def _download_client_pipeline_service(self, rt: BootstrapRuntime) -> DownloadClientPipelineService:
        service_cls = _resolve_service_class(
            rt.cfg,
            "download_client_pipeline_service",
            DownloadClientPipelineService,
        )
        return service_cls(
            log=self.deps.log,
            normalize_url=self.deps.normalize_url,
            wait_for_service=self.deps.wait_for_service,
            bool_cfg=self.deps.bool_cfg,
            invoke_operation=self._invoke_operation,
        )

    def _prepare_download_clients(self, rt: BootstrapRuntime) -> DownloadClientPipelineResult:
        if self._download_client_prepare_result is not None:
            return self._download_client_prepare_result
        self._download_client_prepare_result = self._download_client_pipeline_service(rt).run_prepare(
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
        factory_cls = _resolve_service_class(
            rt.cfg,
            "media_server_adapter_factory",
            MediaServerAdapterFactory,
        )
        factory = factory_cls(
            adapter_class_specs=(rt.adapter_hooks_cfg or {}).get("media_server_adapter_classes"),
        )
        backend = self._canonical_tech_key(str(rt.media_server_backend or ""), rt)
        if not backend:
            media_keys = self._technology_keys_from_hook_map(rt, "media_server_adapter_classes")
            backend = media_keys[0] if media_keys else str(rt.media_server_backend or "").strip()
        if not backend:
            backend = "generic"
        return factory.create(
            backend,
            MediaServerAdapterContext(
                backend=backend,
                runtime=rt,
                invoke_operation=self._invoke_operation,
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
        self._invoke_operation(
            RunnerOperation.RUN_MEDIA_HYGIENE,
            rt.cfg,
            rt.config_root,
            rt.arr_apps_raw,
            rt.app_keys,
            rt.qbit_cfg,
                rt.qb_user,
                rt.qb_pass,
            )
        self._run_lifecycle_phase(
            "clean_hygiene",
            rt,
            keys=self._arr_lifecycle_keys(rt) + self._download_client_lifecycle_keys(rt),
        )
        self.deps.log("[OK] Media hygiene mode complete.")

    def _run_mode_shortcuts(self, rt: BootstrapRuntime) -> bool:
        handlers: dict[BootstrapMode, Callable[[BootstrapRuntime], None]] = {
            BootstrapMode.MEDIA_SERVER_PREWARM: self._run_media_server_prewarm_mode,
            BootstrapMode.MEDIA_SERVER_HOME_RAILS: self._run_media_server_home_rails_mode,
            BootstrapMode.JELLYFIN_PREWARM: self._run_media_server_prewarm_mode,
            BootstrapMode.JELLYFIN_HOME_RAILS: self._run_media_server_home_rails_mode,
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
        self._run_lifecycle_phase("precheck", rt, keys=self._non_servarr_aux_lifecycle_keys(rt))
        self._wait_for_servarr_services(rt)
        self._run_lifecycle_phase("prepare", rt, keys=self._download_client_lifecycle_keys(rt))
        torrent_key = self._canonical_tech_key(rt.torrent_client_key, rt)
        usenet_key = self._canonical_tech_key(rt.usenet_client_key, rt)
        qbit_state = self.lifecycle_manager.state(torrent_key) if self.lifecycle_manager else None
        sab_state = self.lifecycle_manager.state(usenet_key) if self.lifecycle_manager else None
        qbit_login_ok = bool((qbit_state.details if qbit_state else {}).get("login_ok", False))
        sab_api_key = str((sab_state.details if sab_state else {}).get("api_key") or "")
        return qbit_login_ok, sab_api_key

    def _run_servarr_pipeline(
        self, rt: BootstrapRuntime, qbit_login_ok: bool, sab_api_key: str
    ) -> None:
        self._invoke_operation(
            RunnerOperation.RUN_SERVARR_PIPELINE,
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
        self._download_client_prepare_result = None
        self.lifecycle_manager = self._build_lifecycle_manager(rt)
        self._run_lifecycle_phase("load", rt)

        if self._run_mode_shortcuts(rt):
            self._run_lifecycle_phase("status", rt)
            return

        qbit_login_ok, sab_api_key = self._run_full_prechecks(rt)
        self._run_servarr_pipeline(rt, qbit_login_ok=qbit_login_ok, sab_api_key=sab_api_key)
        self._run_lifecycle_phase("configure", rt, keys=self._arr_lifecycle_keys(rt))
        self._run_post_servarr_steps(rt)
        self._run_lifecycle_phase(
            "ensure",
            rt,
            keys=self._arr_lifecycle_keys(rt)
            + self._non_servarr_aux_lifecycle_keys(rt)
            + self._download_client_lifecycle_keys(rt),
        )
        self._run_indexers(rt)
        self._run_lifecycle_phase("status", rt)
        self.deps.log("[OK] Bootstrap complete.")
