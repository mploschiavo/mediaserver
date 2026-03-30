"""Orchestrate shared Servarr bootstrap flow using config-driven adapter hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config_models import AppCapabilities, ServarrAppConfig
from .servarr_adapters import AdapterDependencies, AdapterRegistry, AppBootstrapContext

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
DetectArrApiBaseFn = Callable[[str, str, str], str]
EnsureAppAuthFn = Callable[[str, str, str, str, str, dict[str, Any]], None]
EnsureMediaMgmtFn = Callable[[dict[str, Any], str, str, str, dict[str, Any]], None]
EnsureRootFolderFn = Callable[[str, str, str, str, str], None]
EnsureDownloadHandlingFn = Callable[[str, str, str, str, dict[str, Any]], None]
EnsureQualityUpgradeFn = Callable[
    [dict[str, Any], dict[str, Any], str, str, str, dict[str, Any]],
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
ArrAppLike = ServarrAppConfig | dict[str, Any]


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


@dataclass(frozen=True)
class ClientAuth:
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class ServarrPipelineInputs:
    cfg: dict[str, Any]
    arr_apps: list[ArrAppLike]
    app_keys: dict[str, str]
    prowlarr_url: str
    prowlarr_key: str
    app_auth_cfg: dict[str, Any]
    arr_media_management_cfg: dict[str, Any]
    arr_download_handling_cfg: dict[str, Any]
    arr_quality_upgrade_cfg: dict[str, Any]
    qbit_cfg: dict[str, Any]
    qbit_auth: ClientAuth
    sab_cfg: dict[str, Any]
    sab_auth: ClientAuth
    sab_remote_path_mappings: list[dict[str, Any]]
    adapter_hooks_cfg: dict[str, Any]
    run_cfg: ServarrRunConfig


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

    @staticmethod
    def _coerce_app(app: ArrAppLike) -> ServarrAppConfig:
        if isinstance(app, ServarrAppConfig):
            return app
        if isinstance(app, dict):
            return ServarrAppConfig.from_dict(app)
        raise TypeError(f"Unsupported arr app entry type: {type(app)!r}")

    @staticmethod
    def _raw_app_dict(app: ServarrAppConfig) -> dict[str, Any]:
        if app.raw:
            return dict(app.raw)
        return {
            "name": app.name,
            "implementation": app.implementation,
            "url": app.url,
            "root_folder": app.root_folder,
            "category": app.category,
        }

    @staticmethod
    def _lookup_api_key(app_keys: dict[str, str], implementation: str) -> str:
        key = (
            app_keys.get(implementation)
            or app_keys.get(implementation.lower())
            or app_keys.get(implementation.upper())
        )
        if key is None:
            raise KeyError(f"Missing API key for implementation '{implementation}'")
        return key

    def _apply_auth_settings(
        self,
        app_name: str,
        impl: str,
        app_url: str,
        api_base: str,
        app_key: str,
        app_auth_cfg: dict[str, Any],
    ) -> None:
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

    def _apply_download_clients(
        self,
        app: dict[str, Any],
        app_caps: AppCapabilities,
        app_url: str,
        api_base: str,
        app_key: str,
        inputs: ServarrPipelineInputs,
    ) -> None:
        run_cfg = inputs.run_cfg

        if (
            run_cfg.configure_qbit_arr_clients
            and run_cfg.qbit_login_ok
            and app_caps.supports_download_clients
        ):
            self.ensure_arr_download_client(
                app,
                app_url,
                api_base,
                app_key,
                inputs.qbit_cfg,
                {
                    "username": inputs.qbit_auth.username,
                    "password": inputs.qbit_auth.password,
                },
            )

        if (
            run_cfg.configure_sab_arr_clients
            and run_cfg.sab_api_key
            and app_caps.supports_download_clients
        ):
            self.ensure_arr_download_client(
                app,
                app_url,
                api_base,
                app_key,
                inputs.sab_cfg,
                {
                    "username": inputs.sab_auth.username,
                    "password": inputs.sab_auth.password,
                    "api_key": run_cfg.sab_api_key,
                },
            )
            if app_caps.supports_remote_path_mappings:
                self.ensure_arr_remote_path_mappings(
                    app,
                    app_url,
                    api_base,
                    app_key,
                    inputs.sab_remote_path_mappings,
                )

    def run(self, inputs: ServarrPipelineInputs) -> None:
        run_cfg = inputs.run_cfg
        adapter_registry = AdapterRegistry.from_config(inputs.adapter_hooks_cfg)

        for app_entry in inputs.arr_apps:
            app_model = self._coerce_app(app_entry)
            app = self._raw_app_dict(app_model)
            impl = str(app_model.implementation or "")
            app_url = self.normalize_url(app_model.url or "")
            app_key = self._lookup_api_key(inputs.app_keys, impl)
            app_name = str(app_model.name or impl)
            app_caps = app_model.capabilities
            self.log(f"[STEP] Processing {app_name} ({impl})")
            api_base = self.detect_arr_api_base(app_name, app_url, app_key)

            if app_caps.supports_auth:
                self._apply_auth_settings(
                    app_name,
                    impl,
                    app_url,
                    api_base,
                    app_key,
                    inputs.app_auth_cfg,
                )

            hook = adapter_registry.before_common_steps_for(impl)
            hook(
                self.adapter_deps,
                AppBootstrapContext(
                    cfg=inputs.cfg,
                    app_cfg=app,
                    app_url=app_url,
                    api_base=api_base,
                    api_key=app_key,
                ),
            )

            if run_cfg.configure_arr_media_management and app_caps.supports_media_management:
                self.ensure_arr_media_management(
                    app,
                    app_url,
                    api_base,
                    app_key,
                    inputs.arr_media_management_cfg,
                )

            if app_caps.supports_root_folder:
                self.ensure_root_folder(
                    app_name,
                    app_url,
                    api_base,
                    app_key,
                    app["root_folder"],
                )

            if run_cfg.configure_arr_download_handling and app_caps.supports_download_handling:
                self.ensure_arr_download_handling(
                    app_name,
                    app_url,
                    api_base,
                    app_key,
                    inputs.arr_download_handling_cfg,
                )

            if run_cfg.configure_arr_quality_upgrade and app_caps.supports_quality_upgrade:
                self.ensure_arr_quality_upgrade_policy(
                    inputs.cfg,
                    app,
                    app_url,
                    api_base,
                    app_key,
                    inputs.arr_quality_upgrade_cfg,
                )

            if app_caps.supports_prowlarr_application:
                self.ensure_prowlarr_application(
                    inputs.prowlarr_url,
                    inputs.prowlarr_key,
                    app_name,
                    impl,
                    app_url,
                    app_key,
                )

            self._apply_download_clients(
                app,
                app_caps,
                app_url,
                api_base,
                app_key,
                inputs,
            )

            if run_cfg.configure_arr_discovery_lists and app_caps.supports_discovery_lists:
                self.ensure_arr_discovery_lists_for_app(
                    inputs.cfg,
                    app,
                    app_url,
                    api_base,
                    app_key,
                )
                self.trigger_arr_discovery_kickoff(
                    inputs.cfg,
                    app,
                    app_url,
                    api_base,
                    app_key,
                )

            if run_cfg.refresh_health_after_bootstrap and app_caps.supports_health_check:
                self.trigger_health_check(app_name, app_url, api_base, app_key)
