"""Orchestrate shared Servarr bootstrap flow using per-app adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .servarr_adapters import AdapterDependencies, adapter_for_implementation

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
DetectArrApiBaseFn = Callable[[str, str, str], str]
EnsureAppAuthFn = Callable[[str, str, str, str, str, dict[str, Any]], None]
EnsureMediaMgmtFn = Callable[[dict[str, Any], str, str, str, dict[str, Any]], None]
EnsureRootFolderFn = Callable[[str, str, str, str, str], None]
EnsureDownloadHandlingFn = Callable[[str, str, str, str, dict[str, Any]], None]
EnsureQualityUpgradeFn = Callable[[
    dict[str, Any], dict[str, Any], str, str, str, dict[str, Any]
], None]
EnsureProwlarrAppFn = Callable[[str, str, str, str, str, str], None]
EnsureDownloadClientFn = Callable[[
    dict[str, Any], str, str, str, dict[str, Any], dict[str, Any]
], None]
EnsureRemoteMappingsFn = Callable[[dict[str, Any], str, str, str, list[dict[str, Any]]], None]
EnsureDiscoveryListsFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]
TriggerDiscoveryFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]
TriggerHealthCheckFn = Callable[[str, str, str, str], None]


@dataclass(frozen=True)
class ServarrRunConfig:
    configure_arr_media_management: bool
    configure_arr_download_handling: bool
    configure_arr_quality_upgrade: bool
    configure_arr_discovery_lists: bool
    configure_qbit_arr_clients: bool
    qbit_login_ok: bool
    configure_sab_arr_clients: bool
    sab_api_key: str
    refresh_health_after_bootstrap: bool


@dataclass
class ServarrPipelineService:
    log: LogFn
    normalize_url: NormalizeUrlFn
    detect_arr_api_base: DetectArrApiBaseFn
    ensure_app_auth_settings: EnsureAppAuthFn
    ensure_arr_media_management: EnsureMediaMgmtFn
    ensure_root_folder: EnsureRootFolderFn
    ensure_arr_download_handling: EnsureDownloadHandlingFn
    ensure_arr_quality_upgrade_policy: EnsureQualityUpgradeFn
    ensure_prowlarr_application: EnsureProwlarrAppFn
    ensure_arr_download_client: EnsureDownloadClientFn
    ensure_arr_remote_path_mappings: EnsureRemoteMappingsFn
    ensure_arr_discovery_lists_for_app: EnsureDiscoveryListsFn
    trigger_arr_discovery_kickoff: TriggerDiscoveryFn
    trigger_health_check: TriggerHealthCheckFn
    adapter_deps: AdapterDependencies

    def run(
        self,
        cfg: dict[str, Any],
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        prowlarr_url: str,
        prowlarr_key: str,
        app_auth_cfg: dict[str, Any],
        arr_media_management_cfg: dict[str, Any],
        arr_download_handling_cfg: dict[str, Any],
        arr_quality_upgrade_cfg: dict[str, Any],
        qbit_cfg: dict[str, Any],
        qb_user: str,
        qb_pass: str,
        sab_cfg: dict[str, Any],
        sab_username: str,
        sab_password: str,
        sab_remote_path_mappings: list[dict[str, Any]],
        run_cfg: ServarrRunConfig,
    ) -> None:
        for app in arr_apps:
            impl = str(app.get("implementation") or "")
            app_url = self.normalize_url(app.get("url") or "")
            app_key = app_keys[impl]
            app_name = str(app.get("name") or impl)
            self.log(f"[STEP] Processing {app_name} ({impl})")
            api_base = self.detect_arr_api_base(app_name, app_url, app_key)

            try:
                self.ensure_app_auth_settings(
                    app_name,
                    impl,
                    app_url,
                    api_base,
                    app_key,
                    app_auth_cfg,
                )
            except Exception as exc:
                if bool((app_auth_cfg or {}).get("fail_on_error", False)):
                    raise
                self.log(f"[WARN] {app_name}: auth bootstrap skipped ({exc})")

            adapter = adapter_for_implementation(impl)
            adapter.before_common_steps(
                self.adapter_deps,
                cfg,
                app,
                app_url,
                api_base,
                app_key,
            )

            if run_cfg.configure_arr_media_management:
                self.ensure_arr_media_management(
                    app,
                    app_url,
                    api_base,
                    app_key,
                    arr_media_management_cfg,
                )

            self.ensure_root_folder(app_name, app_url, api_base, app_key, app["root_folder"])

            if run_cfg.configure_arr_download_handling:
                self.ensure_arr_download_handling(
                    app_name,
                    app_url,
                    api_base,
                    app_key,
                    arr_download_handling_cfg,
                )

            if run_cfg.configure_arr_quality_upgrade:
                self.ensure_arr_quality_upgrade_policy(
                    cfg,
                    app,
                    app_url,
                    api_base,
                    app_key,
                    arr_quality_upgrade_cfg,
                )

            self.ensure_prowlarr_application(
                prowlarr_url,
                prowlarr_key,
                app_name,
                impl,
                app_url,
                app_key,
            )

            if run_cfg.configure_qbit_arr_clients and run_cfg.qbit_login_ok:
                self.ensure_arr_download_client(
                    app,
                    app_url,
                    api_base,
                    app_key,
                    qbit_cfg,
                    {
                        "username": qb_user,
                        "password": qb_pass,
                    },
                )

            if run_cfg.configure_sab_arr_clients and run_cfg.sab_api_key:
                self.ensure_arr_download_client(
                    app,
                    app_url,
                    api_base,
                    app_key,
                    sab_cfg,
                    {
                        "username": sab_username,
                        "password": sab_password,
                        "api_key": run_cfg.sab_api_key,
                    },
                )
                self.ensure_arr_remote_path_mappings(
                    app,
                    app_url,
                    api_base,
                    app_key,
                    sab_remote_path_mappings,
                )

            if run_cfg.configure_arr_discovery_lists:
                self.ensure_arr_discovery_lists_for_app(
                    cfg,
                    app,
                    app_url,
                    api_base,
                    app_key,
                )
                self.trigger_arr_discovery_kickoff(
                    cfg,
                    app,
                    app_url,
                    api_base,
                    app_key,
                )

            if run_cfg.refresh_health_after_bootstrap:
                self.trigger_health_check(app_name, app_url, api_base, app_key)
