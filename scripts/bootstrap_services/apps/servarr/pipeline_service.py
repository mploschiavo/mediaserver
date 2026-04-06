"""Orchestrate Servarr bootstrap flow through per-technology adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ...servarr_adapters import AdapterDependencies, AdapterRegistry
from .config_models import (
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from .technologies import (
    ServarrAdapterContext,
    ServarrAdapterDependencies,
    ServarrAdapterFactory,
)
from .types import ClientAuth, ServarrRunConfig

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
ArrAppLike = ServarrAppConfig | dict[str, Any]


@dataclass(frozen=True)
class ServarrPipelineInputs:
    cfg: dict[str, Any]
    arr_apps: list[ArrAppLike]
    app_keys: dict[str, str]
    prowlarr_url: str
    prowlarr_key: str
    app_auth_cfg: dict[str, Any]
    arr_media_management_cfg: ArrMediaManagementPolicy
    arr_download_handling_cfg: ArrDownloadHandlingPolicy
    arr_quality_upgrade_cfg: ArrQualityUpgradePolicy
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
        base = (
            dict(app.raw)
            if app.raw
            else {
                "name": app.name,
                "implementation": app.implementation,
                "url": app.url,
                "root_folder": app.root_folder,
                "category": app.category,
            }
        )
        base_caps = dict(base.get("capabilities") or {})
        caps = {
            "supports_auth": app.capabilities.supports_auth,
            "supports_media_management": app.capabilities.supports_media_management,
            "supports_root_folder": app.capabilities.supports_root_folder,
            "supports_download_handling": app.capabilities.supports_download_handling,
            "supports_quality_upgrade": app.capabilities.supports_quality_upgrade,
            "supports_prowlarr_application": app.capabilities.supports_prowlarr_application,
            "supports_download_clients": app.capabilities.supports_download_clients,
            "supports_remote_path_mappings": app.capabilities.supports_remote_path_mappings,
            "supports_discovery_lists": app.capabilities.supports_discovery_lists,
            "supports_health_check": app.capabilities.supports_health_check,
            "supports_series_folder_management": app.capabilities.supports_series_folder_management,
            "supports_seed_series": app.capabilities.supports_seed_series,
            "monitor_scope_all_value": app.capabilities.monitor_scope_all_value,
            "default_download_category": app.capabilities.default_download_category,
            "download_client_dual_priority_fields": (
                app.capabilities.download_client_dual_priority_fields
            ),
        }
        base_caps.update(caps)
        base["capabilities"] = base_caps
        return base

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

    def _adapter_dependencies(self) -> ServarrAdapterDependencies:
        return ServarrAdapterDependencies(
            log=self.log,
            normalize_url=self.normalize_url,
            detect_arr_api_base=self.detect_arr_api_base,
            ensure_app_auth_settings=self.ensure_app_auth_settings,
            ensure_arr_media_management=self.ensure_arr_media_management,
            ensure_root_folder=self.ensure_root_folder,
            ensure_arr_download_handling=self.ensure_arr_download_handling,
            ensure_arr_quality_upgrade_policy=self.ensure_arr_quality_upgrade_policy,
            ensure_prowlarr_application=self.ensure_prowlarr_application,
            ensure_arr_download_client=self.ensure_arr_download_client,
            ensure_arr_remote_path_mappings=self.ensure_arr_remote_path_mappings,
            ensure_arr_discovery_lists_for_app=self.ensure_arr_discovery_lists_for_app,
            trigger_arr_discovery_kickoff=self.trigger_arr_discovery_kickoff,
            trigger_health_check=self.trigger_health_check,
        )

    def _configure_single_app(self, inputs: ServarrPipelineInputs, app_entry, adapter_factory, adapter_registry) -> None:
        """Configure a single Servarr app (thread-safe)."""
        app_model = self._coerce_app(app_entry)
        impl = str(app_model.implementation or "")
        app_payload = self._raw_app_dict(app_model)
        app_key = self._lookup_api_key(inputs.app_keys, impl)
        adapter = adapter_factory.create(
            context=ServarrAdapterContext(
                cfg=inputs.cfg,
                app_model=app_model,
                app_payload=app_payload,
                app_key=app_key,
                app_auth_cfg=inputs.app_auth_cfg,
                arr_media_management_cfg=inputs.arr_media_management_cfg,
                arr_download_handling_cfg=inputs.arr_download_handling_cfg,
                arr_quality_upgrade_cfg=inputs.arr_quality_upgrade_cfg,
                qbit_cfg=inputs.qbit_cfg,
                qbit_auth=inputs.qbit_auth,
                sab_cfg=inputs.sab_cfg,
                sab_auth=inputs.sab_auth,
                sab_remote_path_mappings=inputs.sab_remote_path_mappings,
                prowlarr_url=inputs.prowlarr_url,
                prowlarr_key=inputs.prowlarr_key,
                run_cfg=inputs.run_cfg,
            ),
            before_common_hook=adapter_registry.before_common_steps_for(impl),
        )
        adapter.load()
        adapter.precheck()
        adapter.prepare()
        adapter.configure()
        adapter.ensure()
        adapter.status_check()

    def run(self, inputs: ServarrPipelineInputs) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        adapter_registry = AdapterRegistry.from_config(inputs.adapter_hooks_cfg)
        adapter_factory = ServarrAdapterFactory(
            deps=self._adapter_dependencies(),
            adapter_deps=self.adapter_deps,
            adapter_class_specs=(inputs.adapter_hooks_cfg or {}).get("adapter_classes"),
        )

        if not inputs.arr_apps:
            return

        if len(inputs.arr_apps) == 1:
            self._configure_single_app(inputs, inputs.arr_apps[0], adapter_factory, adapter_registry)
            return

        self.log(f"[INFO] Configuring {len(inputs.arr_apps)} Arr apps in parallel...")
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=min(4, len(inputs.arr_apps))) as pool:
            futures = {
                pool.submit(
                    self._configure_single_app, inputs, app_entry, adapter_factory, adapter_registry
                ): str(getattr(self._coerce_app(app_entry), "name", "?"))
                for app_entry in inputs.arr_apps
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    self.log(f"[ERR] {name} configuration failed: {exc}")
        if errors:
            raise RuntimeError(f"Servarr pipeline errors: {'; '.join(errors)}")
