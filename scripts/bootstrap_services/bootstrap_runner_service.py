"""Bootstrap orchestration runner extracted from bootstrap-apps entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config_models import (
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from .download_client_pipeline_service import (
    DownloadClientPipelineInputs,
    DownloadClientPipelineResult,
    DownloadClientPipelineService,
)
from .enums import BootstrapMode, RunnerOperation
from .media_server_adapters import MediaServerAdapterContext, MediaServerAdapterFactory
from .runner_operations_service import RunnerOperationRegistry
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
DetectArrApiBaseFn = Callable[[str, str, str], str]
StepActionFn = Callable[[], None]


@dataclass
class BootstrapRunnerDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    detect_arr_api_base: DetectArrApiBaseFn
    operations: RunnerOperationRegistry


@dataclass
class BootstrapRuntime:
    mode: BootstrapMode
    cfg: dict[str, Any]
    config_root: str
    wait_timeout: int
    arr_apps_raw: list[dict[str, Any]]
    arr_apps: list[ServarrAppConfig]
    app_keys: dict[str, str]
    prowlarr_url: str
    prowlarr_key: str
    qbit_cfg: dict[str, Any]
    sab_cfg: dict[str, Any]
    torrent_client_key: str
    usenet_client_key: str
    arr_media_management_cfg: ArrMediaManagementPolicy
    arr_download_handling_cfg: ArrDownloadHandlingPolicy
    arr_quality_upgrade_cfg: ArrQualityUpgradePolicy
    app_auth_cfg: dict[str, Any]
    adapter_hooks_cfg: dict[str, Any]
    prowlarr_indexers: list[dict[str, Any]]
    sab_remote_path_mappings: list[dict[str, Any]]
    qb_user: str
    qb_pass: str
    sab_username: str
    sab_password: str
    auto_indexers: bool
    trigger_sync: bool
    fully_preconfigured: bool
    configure_qbit_arr_clients: bool
    configure_sab_arr_clients: bool
    configure_arr_media_management: bool
    configure_arr_download_handling: bool
    configure_arr_quality_upgrade: bool
    configure_arr_discovery_lists: bool
    set_qbit_categories: bool
    qbit_login_required: bool
    refresh_health_after_bootstrap: bool
    configure_maintainerr_policy: bool
    maintainerr_required: bool
    configure_homepage_services: bool
    homepage_required: bool
    configure_bazarr_integration: bool
    bazarr_required: bool
    configure_jellyseerr_services: bool
    jellyseerr_required: bool
    configure_jellyfin_livetv: bool
    jellyfin_livetv_required: bool
    configure_jellyfin_libraries: bool
    jellyfin_libraries_required: bool
    configure_jellyfin_plugins: bool
    jellyfin_plugins_required: bool
    configure_jellyfin_playback: bool
    jellyfin_playback_required: bool
    configure_jellyfin_home_rails: bool
    jellyfin_home_rails_required: bool
    configure_auto_collections: bool
    auto_collections_required: bool
    configure_disk_guardrails: bool
    disk_guardrails_required: bool
    configure_media_hygiene: bool
    media_hygiene_required: bool
    configure_jellyfin_prewarm: bool
    jellyfin_prewarm_required: bool
    media_server_backend: str = "jellyfin"


@dataclass
class BootstrapRunnerService:
    deps: BootstrapRunnerDependencies
    lifecycle_manager: TechnologyLifecycleManager | None = None
    _download_client_prepare_result: DownloadClientPipelineResult | None = None

    def _invoke_operation(self, operation: RunnerOperation, *args: Any, **kwargs: Any) -> Any:
        return self.deps.operations.invoke(operation, *args, **kwargs)

    @staticmethod
    def _canonical_tech_key(raw: str) -> str:
        token = str(raw or "").strip().lower()
        aliases = {
            "qbittorrent": "qbittorrent",
            "qbit": "qbittorrent",
            "sab": "sabnzbd",
            "sabnzbd": "sabnzbd",
        }
        return aliases.get(token, token)

    def _arr_lifecycle_keys(self, rt: BootstrapRuntime) -> tuple[str, ...]:
        keys = [
            self._canonical_tech_key(app.implementation)
            for app in rt.arr_apps
            if str(app.implementation or "").strip()
        ]
        if not keys:
            keys = ["sonarr", "radarr", "lidarr", "readarr"]
        return tuple(dict.fromkeys(keys))

    def _download_client_lifecycle_keys(self, rt: BootstrapRuntime) -> tuple[str, ...]:
        keys = [
            self._canonical_tech_key(rt.torrent_client_key),
            self._canonical_tech_key(rt.usenet_client_key),
        ]
        return tuple(dict.fromkeys([k for k in keys if k]))

    def _build_lifecycle_manager(self, rt: BootstrapRuntime) -> TechnologyLifecycleManager:
        default_keys = [
            "jellyfin",
            "jellyseerr",
            "bazarr",
            "prowlarr",
            "tautulli",
        ]
        all_keys = list(
            dict.fromkeys(
                default_keys
                + list(self._arr_lifecycle_keys(rt))
                + list(self._download_client_lifecycle_keys(rt))
            )
        )
        lifecycles = {key: TechnologyLifecycle(key=key) for key in all_keys}

        if "prowlarr" in lifecycles:
            lifecycles["prowlarr"].precheck_fn = lambda runtime, state: state.details.update(
                {"api_base": self._ensure_prowlarr_ready(runtime)}
            )
        torrent_key = self._canonical_tech_key(rt.torrent_client_key)
        usenet_key = self._canonical_tech_key(rt.usenet_client_key)
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

        return TechnologyLifecycleManager(lifecycles=lifecycles)

    def _run_lifecycle_phase(
        self,
        phase: str,
        rt: BootstrapRuntime,
        keys: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        if not self.lifecycle_manager:
            return
        self.lifecycle_manager.run_phase(phase, rt, keys=keys)

    def _set_lifecycle_detail(self, key: str, **details: Any) -> None:
        if not self.lifecycle_manager:
            return
        state = self.lifecycle_manager.state(key)
        if not state:
            return
        state.details.update(details)

    def _run_maintainerr_and_homepage_prechecks(self, rt: BootstrapRuntime) -> None:
        self._run_optional_step(
            enabled=rt.configure_maintainerr_policy,
            required=rt.maintainerr_required,
            action=lambda: self._invoke_operation(
                RunnerOperation.ENSURE_MAINTAINERR_POLICY,
                rt.cfg,
                rt.config_root,
            ),
            warning_message=(
                "[WARN] Maintainerr policy: automation skipped. "
                "Set maintainerr.required=true to fail the bootstrap instead."
            ),
        )
        self._run_optional_step(
            enabled=rt.configure_homepage_services,
            required=rt.homepage_required,
            action=lambda: self._invoke_operation(
                RunnerOperation.ENSURE_HOMEPAGE_SERVICES,
                rt.cfg,
                rt.config_root,
            ),
            warning_message=(
                "[WARN] Homepage: config-as-code bootstrap skipped. "
                "Set homepage.required=true to fail the bootstrap instead."
            ),
        )

    def _ensure_prowlarr_ready(self, rt: BootstrapRuntime) -> str:
        self.deps.wait_for_service("Prowlarr", rt.prowlarr_url, "/ping", rt.wait_timeout)
        prowlarr_api_base = self.deps.detect_arr_api_base(
            "Prowlarr",
            rt.prowlarr_url,
            rt.prowlarr_key,
        )
        try:
            self._invoke_operation(
                RunnerOperation.ENSURE_APP_AUTH_SETTINGS,
                "Prowlarr",
                "Prowlarr",
                rt.prowlarr_url,
                prowlarr_api_base,
                rt.prowlarr_key,
                rt.app_auth_cfg,
            )
        except Exception as exc:
            if self.deps.bool_cfg(rt.app_auth_cfg, "fail_on_error", False):
                raise
            self.deps.log(f"[WARN] Prowlarr: auth bootstrap skipped ({exc})")
        return prowlarr_api_base

    def _wait_for_servarr_services(self, rt: BootstrapRuntime) -> None:
        for app in rt.arr_apps:
            self.deps.wait_for_service(app.name, app.url, "/ping", rt.wait_timeout)

    def _download_client_pipeline_service(self) -> DownloadClientPipelineService:
        return DownloadClientPipelineService(
            log=self.deps.log,
            normalize_url=self.deps.normalize_url,
            wait_for_service=self.deps.wait_for_service,
            bool_cfg=self.deps.bool_cfg,
            invoke_operation=self._invoke_operation,
        )

    def _prepare_download_clients(self, rt: BootstrapRuntime) -> DownloadClientPipelineResult:
        if self._download_client_prepare_result is not None:
            return self._download_client_prepare_result
        self._download_client_prepare_result = self._download_client_pipeline_service().run_prepare(
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
        factory = MediaServerAdapterFactory(
            adapter_class_specs=(rt.adapter_hooks_cfg or {}).get("media_server_adapter_classes"),
        )
        backend = str(rt.media_server_backend or "jellyfin").strip().lower() or "jellyfin"
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

    def _run_jellyfin_prewarm_mode(self, rt: BootstrapRuntime) -> None:
        self._media_server_adapter(rt).run_prewarm_mode()

    def _run_jellyfin_home_rails_mode(self, rt: BootstrapRuntime) -> None:
        self._media_server_adapter(rt).run_home_rails_mode()

    def _run_media_hygiene_mode(self, rt: BootstrapRuntime) -> None:
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
        self._run_optional_step(
            enabled=True,
            required=rt.maintainerr_required,
            action=lambda: self._invoke_operation(
                RunnerOperation.ENSURE_MAINTAINERR_POLICY,
                rt.cfg,
                rt.config_root,
            ),
            warning_message=(
                "[WARN] Maintainerr policy: automation skipped. "
                "Set maintainerr.required=true to fail the bootstrap instead."
            ),
        )
        self._run_lifecycle_phase(
            "clean_hygiene",
            rt,
            keys=self._arr_lifecycle_keys(rt) + self._download_client_lifecycle_keys(rt),
        )
        self.deps.log("[OK] Media hygiene mode complete.")

    def _run_mode_shortcuts(self, rt: BootstrapRuntime) -> bool:
        handlers: dict[BootstrapMode, Callable[[BootstrapRuntime], None]] = {
            BootstrapMode.MEDIA_SERVER_PREWARM: self._run_jellyfin_prewarm_mode,
            BootstrapMode.MEDIA_SERVER_HOME_RAILS: self._run_jellyfin_home_rails_mode,
            BootstrapMode.JELLYFIN_PREWARM: self._run_jellyfin_prewarm_mode,
            BootstrapMode.JELLYFIN_HOME_RAILS: self._run_jellyfin_home_rails_mode,
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
        self._run_maintainerr_and_homepage_prechecks(rt)
        self._run_lifecycle_phase("precheck", rt, keys=("prowlarr",))
        self._wait_for_servarr_services(rt)
        self._run_lifecycle_phase("prepare", rt, keys=self._download_client_lifecycle_keys(rt))
        torrent_key = self._canonical_tech_key(rt.torrent_client_key)
        usenet_key = self._canonical_tech_key(rt.usenet_client_key)
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
        pre_steps: list[tuple[bool, bool, StepActionFn, str]] = [
            (
                rt.configure_bazarr_integration,
                rt.bazarr_required,
                lambda: self._invoke_operation(
                    RunnerOperation.ENSURE_BAZARR_INTEGRATION,
                    rt.cfg,
                    rt.config_root,
                    rt.arr_apps_raw,
                    rt.app_keys,
                    rt.wait_timeout,
                ),
                "[WARN] Bazarr: integration bootstrap skipped. Set bazarr.required=true to fail the bootstrap instead.",
            ),
            (
                rt.configure_jellyseerr_services,
                rt.jellyseerr_required,
                lambda: self._invoke_operation(
                    RunnerOperation.CONFIGURE_JELLYSEERR,
                    rt.cfg,
                    rt.arr_apps_raw,
                    rt.app_keys,
                    rt.config_root,
                    rt.wait_timeout,
                ),
                "[WARN] Jellyseerr: automation skipped. Set jellyseerr.required=true to fail the bootstrap instead.",
            ),
        ]

        for enabled, required, action, warning_message in pre_steps:
            self._run_optional_step(
                enabled=enabled,
                required=required,
                action=action,
                warning_message=warning_message,
            )

        self._media_server_adapter(rt).run_post_servarr_pre_hygiene_steps()

        post_media_steps: list[tuple[bool, bool, StepActionFn, str]] = [
            (
                rt.configure_disk_guardrails,
                rt.disk_guardrails_required,
                lambda: self._invoke_operation(
                    RunnerOperation.ENFORCE_DISK_GUARDRAILS,
                    rt.cfg,
                    rt.config_root,
                    rt.qbit_cfg,
                    rt.qb_user,
                    rt.qb_pass,
                ),
                "[WARN] Disk guardrails: automation skipped. Set disk_guardrails.required=true to fail the bootstrap instead.",
            ),
            (
                rt.configure_media_hygiene,
                rt.media_hygiene_required,
                lambda: self._invoke_operation(
                    RunnerOperation.RUN_MEDIA_HYGIENE,
                    rt.cfg,
                    rt.config_root,
                    rt.arr_apps_raw,
                    rt.app_keys,
                    rt.qbit_cfg,
                    rt.qb_user,
                    rt.qb_pass,
                ),
                "[WARN] Media hygiene: automation skipped. Set media_hygiene.required=true to fail the bootstrap instead.",
            ),
        ]
        for enabled, required, action, warning_message in post_media_steps:
            self._run_optional_step(
                enabled=enabled,
                required=required,
                action=action,
                warning_message=warning_message,
            )
        self._media_server_adapter(rt).run_post_servarr_post_hygiene_steps()

    def _run_indexers(self, rt: BootstrapRuntime) -> None:
        indexer_failures = 0
        for indexer in rt.prowlarr_indexers:
            idx_name = indexer.get("name") or indexer.get("implementation") or "unnamed-indexer"
            try:
                self._invoke_operation(
                    RunnerOperation.ENSURE_PROWLARR_INDEXER,
                    rt.prowlarr_url,
                    rt.prowlarr_key,
                    indexer,
                )
            except Exception as exc:
                indexer_failures += 1
                self.deps.log(f"[WARN] Prowlarr: failed indexer '{idx_name}': {exc}")

        if indexer_failures:
            if bool(rt.cfg.get("fail_on_indexer_error", False)):
                raise RuntimeError(
                    f"Prowlarr: {indexer_failures} configured indexer(s) failed and fail_on_indexer_error=true."
                )
            self.deps.log(
                f"[WARN] Prowlarr: {indexer_failures} configured indexer(s) failed; "
                "continuing because fail_on_indexer_error is false."
            )

        if rt.auto_indexers:
            self._invoke_operation(
                RunnerOperation.AUTO_ADD_TESTED_INDEXERS,
                rt.prowlarr_url,
                rt.prowlarr_key,
            )

        if rt.trigger_sync:
            self._invoke_operation(
                RunnerOperation.TRIGGER_PROWLARR_SYNC,
                rt.prowlarr_url,
                rt.prowlarr_key,
            )

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
            + (
                "bazarr",
                "jellyseerr",
                "jellyfin",
            )
            + self._download_client_lifecycle_keys(rt),
        )
        self._run_indexers(rt)
        self._run_lifecycle_phase("status", rt)
        self.deps.log("[OK] Bootstrap complete.")
