"""Base Servarr adapter contract and shared lifecycle behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..config_models import (
    AppCapabilities,
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from ..servarr_adapters import AdapterDependencies, AppBootstrapContext, HookFn
from ..servarr_types import ClientAuth, ServarrRunConfig

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
DetectArrApiBaseFn = Callable[[str, str, str], str]
EnsureAppAuthFn = Callable[[str, str, str, str, str, dict[str, Any]], None]
EnsureMediaMgmtFn = Callable[
    [ServarrAppConfig | dict[str, Any], str, str, str, ArrMediaManagementPolicy],
    None,
]
EnsureRootFolderFn = Callable[[str, str, str, str, str], None]
EnsureDownloadHandlingFn = Callable[
    [ServarrAppConfig | dict[str, Any] | str, str, str, str, ArrDownloadHandlingPolicy],
    None,
]
EnsureQualityUpgradeFn = Callable[
    [
        dict[str, Any],
        ServarrAppConfig | dict[str, Any],
        str,
        str,
        str,
        ArrQualityUpgradePolicy,
    ],
    None,
]
EnsureProwlarrAppFn = Callable[[str, str, str, str, str, str], None]
EnsureDownloadClientFn = Callable[
    [dict[str, Any], str, str, str, dict[str, Any], dict[str, Any]],
    None,
]
EnsureRemoteMappingsFn = Callable[[dict[str, Any], str, str, str, list[dict[str, Any]]], None]
EnsureDiscoveryListsFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]
TriggerDiscoveryFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]
TriggerHealthCheckFn = Callable[[str, str, str, str], None]


@dataclass(frozen=True)
class ServarrAdapterDependencies:
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


@dataclass
class ServarrAdapterContext:
    cfg: dict[str, Any]
    app_model: ServarrAppConfig
    app_payload: dict[str, Any]
    app_key: str
    app_auth_cfg: dict[str, Any]
    arr_media_management_cfg: ArrMediaManagementPolicy
    arr_download_handling_cfg: ArrDownloadHandlingPolicy
    arr_quality_upgrade_cfg: ArrQualityUpgradePolicy
    qbit_cfg: dict[str, Any]
    qbit_auth: ClientAuth
    sab_cfg: dict[str, Any]
    sab_auth: ClientAuth
    sab_remote_path_mappings: list[dict[str, Any]]
    prowlarr_url: str
    prowlarr_key: str
    run_cfg: ServarrRunConfig
    app_url: str = ""
    api_base: str = ""

    @property
    def app_name(self) -> str:
        return str(self.app_model.name or self.app_model.implementation)

    @property
    def app_impl(self) -> str:
        return str(self.app_model.implementation or "")

    @property
    def app_caps(self) -> AppCapabilities:
        return self.app_model.capabilities


@dataclass
class ServarrAdapterBase:
    context: ServarrAdapterContext
    deps: ServarrAdapterDependencies
    adapter_deps: AdapterDependencies
    before_common_hook: HookFn

    def load(self) -> None:
        self.context.app_url = self.deps.normalize_url(self.context.app_model.url or "")
        self.deps.log(f"[STEP] Processing {self.context.app_name} ({self.context.app_impl})")

    def _apply_auth_settings(self) -> None:
        try:
            self.deps.ensure_app_auth_settings(
                self.context.app_name,
                self.context.app_impl,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.app_auth_cfg,
            )
        except Exception as exc:
            if bool((self.context.app_auth_cfg or {}).get("fail_on_error", False)):
                raise
            self.deps.log(f"[WARN] {self.context.app_name}: auth bootstrap skipped ({exc})")

    def precheck(self) -> None:
        self.context.api_base = self.deps.detect_arr_api_base(
            self.context.app_name,
            self.context.app_url,
            self.context.app_key,
        )

        if self.context.app_caps.supports_auth:
            self._apply_auth_settings()

        self.before_common_hook(
            self.adapter_deps,
            AppBootstrapContext(
                cfg=self.context.cfg,
                app_cfg=self.context.app_payload,
                app_url=self.context.app_url,
                api_base=self.context.api_base,
                api_key=self.context.app_key,
            ),
        )

    def prepare(self) -> None:
        return

    def _configure_download_clients(self) -> None:
        run_cfg = self.context.run_cfg
        if (
            run_cfg.configure_qbit_arr_clients
            and run_cfg.qbit_login_ok
            and self.context.app_caps.supports_download_clients
        ):
            self.deps.ensure_arr_download_client(
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.qbit_cfg,
                {
                    "username": self.context.qbit_auth.username,
                    "password": self.context.qbit_auth.password,
                },
            )

        if (
            run_cfg.configure_sab_arr_clients
            and self.context.app_caps.supports_download_clients
        ):
            usenet_auth = {
                "username": self.context.sab_auth.username,
                "password": self.context.sab_auth.password,
            }
            if run_cfg.sab_api_key:
                usenet_auth["api_key"] = run_cfg.sab_api_key
            self.deps.ensure_arr_download_client(
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.sab_cfg,
                usenet_auth,
            )
            if self.context.app_caps.supports_remote_path_mappings and self.context.sab_remote_path_mappings:
                self.deps.ensure_arr_remote_path_mappings(
                    self.context.app_payload,
                    self.context.app_url,
                    self.context.api_base,
                    self.context.app_key,
                    self.context.sab_remote_path_mappings,
                )

    def configure(self) -> None:
        run_cfg = self.context.run_cfg

        if run_cfg.configure_arr_media_management and self.context.app_caps.supports_media_management:
            self.deps.ensure_arr_media_management(
                self.context.app_model,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.arr_media_management_cfg,
            )

        if self.context.app_caps.supports_root_folder:
            self.deps.ensure_root_folder(
                self.context.app_name,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.app_payload["root_folder"],
            )

        if (
            run_cfg.configure_arr_download_handling
            and self.context.app_caps.supports_download_handling
        ):
            self.deps.ensure_arr_download_handling(
                self.context.app_model,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.arr_download_handling_cfg,
            )

        if run_cfg.configure_arr_quality_upgrade and self.context.app_caps.supports_quality_upgrade:
            self.deps.ensure_arr_quality_upgrade_policy(
                self.context.cfg,
                self.context.app_model,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.arr_quality_upgrade_cfg,
            )

        if self.context.app_caps.supports_prowlarr_application:
            self.deps.ensure_prowlarr_application(
                self.context.prowlarr_url,
                self.context.prowlarr_key,
                self.context.app_name,
                self.context.app_impl,
                self.context.app_url,
                self.context.app_key,
            )

        self._configure_download_clients()

        if run_cfg.configure_arr_discovery_lists and self.context.app_caps.supports_discovery_lists:
            self.deps.ensure_arr_discovery_lists_for_app(
                self.context.cfg,
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
            )

    def ensure(self) -> None:
        run_cfg = self.context.run_cfg
        if run_cfg.configure_arr_discovery_lists and self.context.app_caps.supports_discovery_lists:
            self.deps.trigger_arr_discovery_kickoff(
                self.context.cfg,
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
            )

        if run_cfg.refresh_health_after_bootstrap and self.context.app_caps.supports_health_check:
            self.deps.trigger_health_check(
                self.context.app_name,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
            )

    def status_check(self) -> dict[str, Any]:
        return {
            "implementation": self.context.app_impl,
            "api_base": self.context.api_base,
        }

    def clean_hygiene(self) -> None:
        return
