"""Build typed bootstrap runtime state from CLI args and config."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config_models import (
    AppAuthConfig,
    ArrDiscoveryListsConfig,
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    BazarrConfig,
    DiskGuardrailsConfig,
    DownloadClientsConfig,
    HomepageConfig,
    JellyfinAutoCollectionsConfig,
    JellyfinHomeRailsConfig,
    JellyfinLibrariesConfig,
    JellyfinLiveTvConfig,
    JellyfinPlaybackConfig,
    JellyfinPluginsConfig,
    JellyfinPrewarmConfig,
    JellyseerrConfig,
    MediaHygieneConfig,
    MaintainerrConfig,
    ServarrAppConfig,
    TechnologyBindingsConfig,
)
from ..enums import BootstrapMode
from ..runtime_models import BootstrapRuntime
from ..technology_catalog import default_servarr_catalog
from ..top_level_config_model import TopLevelBootstrapConfig
from .bindings_resolution import RuntimeBindingResolver

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
DeepMergeFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
LoadDefaultJsonFn = Callable[[str, Any], Any]
EnvTruthyFn = Callable[[str, bool], bool]
ReadApiKeyFn = Callable[[str, str], str]
BuildSabMappingsFn = Callable[[dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class BootstrapCliArgs:
    mode: BootstrapMode
    config_path: str
    config_root: str
    wait_timeout: int
    auto_prowlarr_indexers: bool
    runtime_env: str = "prod"


@dataclass(frozen=True)
class BootstrapPlanSummary:
    mode: BootstrapMode
    arr_apps: int
    prowlarr_indexers: int
    auto_indexers: bool
    configure_arr_clients: bool
    configure_qbit_arr_clients: bool
    configure_sab_arr_clients: bool
    sab_remote_path_mappings: int
    configure_arr_media_management: bool
    configure_arr_quality_upgrade: bool
    configure_arr_download_handling: bool
    configure_arr_discovery_lists: bool
    set_qbit_categories: bool
    qbit_login_required: bool
    refresh_health_after_bootstrap: bool
    app_auth_enabled: bool
    configure_homepage: bool
    configure_bazarr: bool
    configure_jellyseerr: bool
    configure_jellyfin_libraries: bool
    configure_jellyfin_livetv: bool
    configure_jellyfin_plugins: bool
    configure_jellyfin_playback: bool
    configure_jellyfin_home_rails: bool
    configure_auto_collections: bool
    configure_disk_guardrails: bool
    configure_jellyfin_prewarm: bool
    configure_media_hygiene: bool
    configure_maintainerr_policy: bool
    configure_maintainerr_integrations: bool
    jellyfin_livetv_tuners: int
    jellyfin_livetv_guides: int
    fully_preconfigured: bool
    trigger_sync: bool

    def to_log_line(self) -> str:
        return (
            f"mode={self.mode.value}, "
            f"arr_apps={self.arr_apps}, "
            f"prowlarr_indexers={self.prowlarr_indexers}, "
            f"auto_indexers={self.auto_indexers}, "
            f"configure_arr_clients={self.configure_arr_clients}, "
            f"configure_qbit_arr_clients={self.configure_qbit_arr_clients}, "
            f"configure_sab_arr_clients={self.configure_sab_arr_clients}, "
            f"sab_remote_path_mappings={self.sab_remote_path_mappings}, "
            f"configure_arr_media_management={self.configure_arr_media_management}, "
            f"configure_arr_quality_upgrade={self.configure_arr_quality_upgrade}, "
            f"configure_arr_download_handling={self.configure_arr_download_handling}, "
            f"configure_arr_discovery_lists={self.configure_arr_discovery_lists}, "
            f"set_qbit_categories={self.set_qbit_categories}, "
            f"qbit_login_required={self.qbit_login_required}, "
            f"refresh_health_after_bootstrap={self.refresh_health_after_bootstrap}, "
            f"app_auth_enabled={self.app_auth_enabled}, "
            f"configure_homepage={self.configure_homepage}, "
            f"configure_bazarr={self.configure_bazarr}, "
            f"configure_jellyseerr={self.configure_jellyseerr}, "
            f"configure_jellyfin_libraries={self.configure_jellyfin_libraries}, "
            f"configure_jellyfin_livetv={self.configure_jellyfin_livetv}, "
            f"configure_jellyfin_plugins={self.configure_jellyfin_plugins}, "
            f"configure_jellyfin_playback={self.configure_jellyfin_playback}, "
            f"configure_jellyfin_home_rails={self.configure_jellyfin_home_rails}, "
            f"configure_auto_collections={self.configure_auto_collections}, "
            f"configure_disk_guardrails={self.configure_disk_guardrails}, "
            f"configure_jellyfin_prewarm={self.configure_jellyfin_prewarm}, "
            f"configure_media_hygiene={self.configure_media_hygiene}, "
            f"configure_maintainerr_policy={self.configure_maintainerr_policy}, "
            f"configure_maintainerr_integrations={self.configure_maintainerr_integrations}, "
            f"jellyfin_livetv_tuners={self.jellyfin_livetv_tuners}, "
            f"jellyfin_livetv_guides={self.jellyfin_livetv_guides}, "
            f"fully_preconfigured={self.fully_preconfigured}, "
            f"trigger_sync={self.trigger_sync}"
        )


@dataclass(frozen=True)
class BootstrapRuntimeBuildResult:
    cfg: dict[str, Any]
    runtime: BootstrapRuntime
    plan: BootstrapPlanSummary


@dataclass
class BootstrapRuntimeFactoryDependencies:
    load_bootstrap_default_json: LoadDefaultJsonFn
    deep_merge_objects: DeepMergeFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    env_truthy: EnvTruthyFn
    read_api_key: ReadApiKeyFn
    build_sab_remote_path_mappings: BuildSabMappingsFn


@dataclass
class BootstrapRuntimeFactoryService:
    deps: BootstrapRuntimeFactoryDependencies

    def _find_repo_root(self, start_path: Path) -> Path:
        for candidate in [start_path, *start_path.parents]:
            if (candidate / "bootstrap").is_dir() and (candidate / "scripts").is_dir():
                return candidate
        return start_path.parent

    def _resolve_path(self, root_dir: Path, raw_path: str) -> Path:
        candidate = Path(str(raw_path or "").strip())
        if candidate.is_absolute():
            return candidate
        return (root_dir / candidate).resolve()

    def load_config(self, config_path: str, runtime_env: str = "prod") -> dict[str, Any]:
        config_file = Path(config_path).resolve()
        loaded = json.loads(config_file.read_text(encoding="utf-8"))
        model = TopLevelBootstrapConfig.from_dict(loaded)

        overlay_cfg = model.config_overlays
        selected_env = (
            str(runtime_env or "").strip().lower()
            or str(overlay_cfg.env or "").strip().lower()
            or str(os.environ.get("MEDIA_STACK_ENV", "")).strip().lower()
            or "prod"
        )

        root_dir = self._find_repo_root(config_file.parent)

        if not overlay_cfg.enabled:
            return model.to_dict()

        merged: dict[str, Any] = {}
        base_path = self._resolve_path(root_dir, overlay_cfg.base_path)
        if base_path.exists():
            base_cfg = json.loads(base_path.read_text(encoding="utf-8"))
            merged = self.deps.deep_merge_objects(merged, dict(base_cfg))

        overlay_filename = overlay_cfg.env_overlays.get(selected_env, f"{selected_env}.json")
        overlay_path = self._resolve_path(
            root_dir,
            str(Path(overlay_cfg.overlay_dir) / overlay_filename),
        )
        if overlay_path.exists():
            overlay_cfg_data = json.loads(overlay_path.read_text(encoding="utf-8"))
            merged = self.deps.deep_merge_objects(merged, dict(overlay_cfg_data))

        merged = self.deps.deep_merge_objects(merged, model.to_dict())
        return TopLevelBootstrapConfig.from_dict(merged).to_dict()

    def build_from_cli(self, args: BootstrapCliArgs) -> BootstrapRuntimeBuildResult:
        return self.build(args, self.load_config(args.config_path, runtime_env=args.runtime_env))

    def build(self, args: BootstrapCliArgs, cfg: dict[str, Any]) -> BootstrapRuntimeBuildResult:
        cfg = TopLevelBootstrapConfig.from_dict(cfg).to_dict()
        prowlarr_url = str(cfg.get("prowlarr_url") or "").strip().rstrip("/")
        arr_apps_raw = cfg.get("arr_apps", [])

        catalog = default_servarr_catalog()
        arr_default_capability_defaults = self.deps.load_bootstrap_default_json(
            "app_capability_defaults.json",
            {},
        )
        arr_app_capability_defaults = catalog.expand_capability_defaults(
            self.deps.deep_merge_objects(
                arr_default_capability_defaults,
                cfg.get("app_capability_defaults") or {},
            )
        )
        arr_apps = ServarrAppConfig.from_list(
            arr_apps_raw,
            capability_defaults=arr_app_capability_defaults,
        )

        download_clients_model = DownloadClientsConfig.from_dict(cfg.get("download_clients") or {})
        download_clients_cfg = download_clients_model.raw
        media_server_cfg = cfg.get("media_server") or {}

        default_media_server_operation_plans = self.deps.load_bootstrap_default_json(
            "media_server_operation_plans.json",
            {},
        )
        default_runner_operation_plans = self.deps.load_bootstrap_default_json(
            "runner_operation_plans.json",
            {},
        )

        adapter_hooks_cfg = self.deps.deep_merge_objects(
            self.deps.load_bootstrap_default_json(
                "adapter_hooks.json",
                {},
            ),
            {
                "media_server_operation_plans": default_media_server_operation_plans,
                "runner_operation_plans": default_runner_operation_plans,
            },
        )
        adapter_hooks_cfg = self.deps.deep_merge_objects(
            adapter_hooks_cfg,
            cfg.get("adapter_hooks") or {},
        )
        adapter_hooks_cfg = self.deps.deep_merge_objects(
            adapter_hooks_cfg,
            {
                "media_server_operation_plans": media_server_cfg.get("operation_plans") or {},
            },
        )
        bindings_cfg = TechnologyBindingsConfig.from_dict(
            cfg.get("technology_bindings") or {},
        )
        binding_resolution = RuntimeBindingResolver().resolve(
            technology_bindings=bindings_cfg,
            adapter_hooks_cfg=adapter_hooks_cfg,
            download_clients=download_clients_model,
            media_server_cfg=media_server_cfg,
        )
        torrent_client_key = binding_resolution.torrent_client_key
        usenet_client_key = binding_resolution.usenet_client_key
        qbit_cfg = dict(binding_resolution.torrent_client_cfg)
        sab_cfg = dict(binding_resolution.usenet_client_cfg)

        arr_download_handling_cfg = ArrDownloadHandlingPolicy.from_dict(
            cfg.get("arr_download_handling") or {},
            canonicalize=catalog.canonicalize,
        )
        arr_media_management_cfg = ArrMediaManagementPolicy.from_dict(
            cfg.get("arr_media_management") or {},
            canonicalize=catalog.canonicalize,
        )
        arr_quality_upgrade_cfg = ArrQualityUpgradePolicy.from_dict(
            cfg.get("arr_quality_upgrade") or {},
            canonicalize=catalog.canonicalize,
        )
        arr_discovery_lists_cfg = ArrDiscoveryListsConfig.from_dict(
            cfg.get("arr_discovery_lists") or {}
        )

        jellyseerr_model = JellyseerrConfig.from_dict(cfg.get("jellyseerr") or {})
        homepage_model = HomepageConfig.from_dict(cfg.get("homepage") or {})
        bazarr_model = BazarrConfig.from_dict(cfg.get("bazarr") or {})
        jellyfin_libraries_model = JellyfinLibrariesConfig.from_dict(
            cfg.get("jellyfin_libraries") or {}
        )
        jellyfin_livetv_model = JellyfinLiveTvConfig.from_dict(cfg.get("jellyfin_livetv") or {})
        jellyfin_livetv_cfg = jellyfin_livetv_model.raw
        jellyfin_plugins_model = JellyfinPluginsConfig.from_dict(cfg.get("jellyfin_plugins") or {})
        jellyfin_playback_model = JellyfinPlaybackConfig.from_dict(
            cfg.get("jellyfin_playback") or {}
        )
        jellyfin_home_rails_model = JellyfinHomeRailsConfig.from_dict(
            cfg.get("jellyfin_home_rails") or {}
        )
        jellyfin_auto_collections_model = JellyfinAutoCollectionsConfig.from_dict(
            cfg.get("jellyfin_auto_collections") or {}
        )
        jellyfin_prewarm_model = JellyfinPrewarmConfig.from_dict(cfg.get("jellyfin_prewarm") or {})
        disk_guardrails_model = DiskGuardrailsConfig.from_dict(cfg.get("disk_guardrails") or {})
        media_hygiene_model = MediaHygieneConfig.from_dict(cfg.get("media_hygiene") or {})
        maintainerr_model = MaintainerrConfig.from_dict(cfg.get("maintainerr") or {})

        media_server_backend = binding_resolution.media_server_backend

        app_auth_model = AppAuthConfig.from_dict(cfg.get("app_auth") or {})
        app_auth_cfg = dict(app_auth_model.raw)
        fully_preconfigured = self.deps.env_truthy("FULLY_PRECONFIGURED", False)
        if fully_preconfigured and not app_auth_cfg:
            app_auth_cfg = {
                "enabled": True,
                "method": "Forms",
                "required": "Enabled",
                "username_env": "STACK_ADMIN_USERNAME",
                "password_env": "STACK_ADMIN_PASSWORD",
                "include": ["Sonarr", "Radarr", "Lidarr", "Readarr", "Prowlarr"],
            }
        app_auth_model = AppAuthConfig.from_dict(app_auth_cfg)

        prowlarr_indexers = cfg.get("prowlarr_indexers", [])
        auto_indexers = bool(
            cfg.get("prowlarr_auto_add_tested_indexers", False) or args.auto_prowlarr_indexers
        )

        configure_qbit_arr_clients = bool(qbit_cfg.get("configure_arr_clients", False))
        configure_sab_arr_clients = bool(sab_cfg.get("configure_arr_clients", False))
        configure_arr_clients = configure_qbit_arr_clients or configure_sab_arr_clients

        sab_remote_path_mappings = (
            self.deps.build_sab_remote_path_mappings(sab_cfg) if configure_sab_arr_clients else []
        )

        configure_arr_media_management = arr_media_management_cfg.enabled
        configure_arr_quality_upgrade = arr_quality_upgrade_cfg.enabled
        configure_arr_download_handling = arr_download_handling_cfg.enabled
        configure_arr_discovery_lists = arr_discovery_lists_cfg.enabled
        set_qbit_categories = bool(
            qbit_cfg.get("set_categories_in_qbit", qbit_cfg.get("set_categories", False))
        )
        qbit_login_required = bool(qbit_cfg.get("login_required", fully_preconfigured))
        refresh_health_after_bootstrap = bool(cfg.get("refresh_health_after_bootstrap", True))

        configure_jellyseerr_services = jellyseerr_model.enabled
        jellyseerr_required = jellyseerr_model.required

        configure_homepage_services = homepage_model.enabled or bool(homepage_model.hosts)
        homepage_required = homepage_model.required

        configure_bazarr_integration = bazarr_model.enabled
        bazarr_required = bazarr_model.required

        configure_jellyfin_libraries = jellyfin_libraries_model.enabled
        jellyfin_libraries_required = jellyfin_libraries_model.required

        configure_jellyfin_livetv = jellyfin_livetv_model.enabled
        jellyfin_livetv_required = jellyfin_livetv_model.required

        configure_jellyfin_plugins = jellyfin_plugins_model.enabled
        jellyfin_plugins_required = jellyfin_plugins_model.required

        configure_jellyfin_playback = jellyfin_playback_model.enabled
        jellyfin_playback_required = jellyfin_playback_model.required

        configure_jellyfin_home_rails = (
            jellyfin_home_rails_model.enabled
            or jellyfin_home_rails_model.cleanup_collections_when_disabled
        )
        jellyfin_home_rails_required = jellyfin_home_rails_model.required

        configure_auto_collections = jellyfin_auto_collections_model.enabled
        auto_collections_required = jellyfin_auto_collections_model.required

        configure_disk_guardrails = disk_guardrails_model.enabled
        disk_guardrails_required = disk_guardrails_model.required

        configure_jellyfin_prewarm = jellyfin_prewarm_model.enabled
        jellyfin_prewarm_required = jellyfin_prewarm_model.required

        configure_media_hygiene = media_hygiene_model.enabled
        media_hygiene_required = media_hygiene_model.required

        configure_maintainerr_policy = maintainerr_model.enabled
        maintainerr_required = maintainerr_model.required
        configure_maintainerr_integrations = maintainerr_model.integrations.enabled
        maintainerr_integrations_required = maintainerr_model.integrations.required

        trigger_sync = bool(cfg.get("trigger_indexer_sync", True))

        app_keys: dict[str, str] = {}
        prowlarr_key = ""
        if args.mode in (BootstrapMode.FULL, BootstrapMode.MEDIA_HYGIENE):
            for app in arr_apps:
                app_dir = app.implementation.lower()
                api_key = self.deps.read_api_key(args.config_root, app_dir)
                app_keys[app.implementation] = api_key
                app_keys[app.implementation.lower()] = api_key

        if args.mode == BootstrapMode.FULL:
            prowlarr_key = self.deps.read_api_key(args.config_root, "prowlarr")

        torrent_username_env = str(qbit_cfg.get("username_env", "STACK_ADMIN_USERNAME")).strip()
        torrent_password_env = str(qbit_cfg.get("password_env", "STACK_ADMIN_PASSWORD")).strip()
        qb_user = (
            os.environ.get(torrent_username_env)
            or os.environ.get("STACK_ADMIN_USERNAME")
            or "admin"
        )
        qb_pass = (
            os.environ.get(torrent_password_env)
            or os.environ.get("STACK_ADMIN_PASSWORD")
            or "media-stack-admin"
        )
        sab_username = (
            os.environ.get(str(sab_cfg.get("username_env", "SABNZBD_USERNAME"))) or ""
        ).strip()
        sab_password = (
            os.environ.get(str(sab_cfg.get("password_env", "SABNZBD_PASSWORD"))) or ""
        ).strip()

        runtime = BootstrapRuntime(
            mode=args.mode,
            cfg=cfg,
            config_root=args.config_root,
            wait_timeout=args.wait_timeout,
            arr_apps_raw=arr_apps_raw,
            arr_apps=arr_apps,
            app_keys=app_keys,
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            qbit_cfg=qbit_cfg,
            sab_cfg=sab_cfg,
            torrent_client_key=torrent_client_key,
            usenet_client_key=usenet_client_key,
            arr_media_management_cfg=arr_media_management_cfg,
            arr_download_handling_cfg=arr_download_handling_cfg,
            arr_quality_upgrade_cfg=arr_quality_upgrade_cfg,
            app_auth_cfg=app_auth_cfg,
            adapter_hooks_cfg=adapter_hooks_cfg,
            prowlarr_indexers=prowlarr_indexers,
            sab_remote_path_mappings=sab_remote_path_mappings,
            qb_user=qb_user,
            qb_pass=qb_pass,
            sab_username=sab_username,
            sab_password=sab_password,
            auto_indexers=auto_indexers,
            trigger_sync=trigger_sync,
            fully_preconfigured=fully_preconfigured,
            configure_qbit_arr_clients=configure_qbit_arr_clients,
            configure_sab_arr_clients=configure_sab_arr_clients,
            configure_arr_media_management=configure_arr_media_management,
            configure_arr_download_handling=configure_arr_download_handling,
            configure_arr_quality_upgrade=configure_arr_quality_upgrade,
            configure_arr_discovery_lists=configure_arr_discovery_lists,
            set_qbit_categories=set_qbit_categories,
            qbit_login_required=qbit_login_required,
            refresh_health_after_bootstrap=refresh_health_after_bootstrap,
            configure_maintainerr_policy=configure_maintainerr_policy,
            maintainerr_required=maintainerr_required,
            configure_maintainerr_integrations=configure_maintainerr_integrations,
            maintainerr_integrations_required=maintainerr_integrations_required,
            configure_homepage_services=configure_homepage_services,
            homepage_required=homepage_required,
            configure_bazarr_integration=configure_bazarr_integration,
            bazarr_required=bazarr_required,
            configure_jellyseerr_services=configure_jellyseerr_services,
            jellyseerr_required=jellyseerr_required,
            configure_jellyfin_livetv=configure_jellyfin_livetv,
            jellyfin_livetv_required=jellyfin_livetv_required,
            configure_jellyfin_libraries=configure_jellyfin_libraries,
            jellyfin_libraries_required=jellyfin_libraries_required,
            configure_jellyfin_plugins=configure_jellyfin_plugins,
            jellyfin_plugins_required=jellyfin_plugins_required,
            configure_jellyfin_playback=configure_jellyfin_playback,
            jellyfin_playback_required=jellyfin_playback_required,
            configure_jellyfin_home_rails=configure_jellyfin_home_rails,
            jellyfin_home_rails_required=jellyfin_home_rails_required,
            configure_auto_collections=configure_auto_collections,
            auto_collections_required=auto_collections_required,
            configure_disk_guardrails=configure_disk_guardrails,
            disk_guardrails_required=disk_guardrails_required,
            configure_media_hygiene=configure_media_hygiene,
            media_hygiene_required=media_hygiene_required,
            configure_jellyfin_prewarm=configure_jellyfin_prewarm,
            jellyfin_prewarm_required=jellyfin_prewarm_required,
            media_server_backend=media_server_backend,
        )

        plan = BootstrapPlanSummary(
            mode=args.mode,
            arr_apps=len(arr_apps),
            prowlarr_indexers=len(prowlarr_indexers),
            auto_indexers=auto_indexers,
            configure_arr_clients=configure_arr_clients,
            configure_qbit_arr_clients=configure_qbit_arr_clients,
            configure_sab_arr_clients=configure_sab_arr_clients,
            sab_remote_path_mappings=len(sab_remote_path_mappings),
            configure_arr_media_management=configure_arr_media_management,
            configure_arr_quality_upgrade=configure_arr_quality_upgrade,
            configure_arr_download_handling=configure_arr_download_handling,
            configure_arr_discovery_lists=configure_arr_discovery_lists,
            set_qbit_categories=set_qbit_categories,
            qbit_login_required=qbit_login_required,
            refresh_health_after_bootstrap=refresh_health_after_bootstrap,
            app_auth_enabled=app_auth_model.enabled,
            configure_homepage=configure_homepage_services,
            configure_bazarr=configure_bazarr_integration,
            configure_jellyseerr=configure_jellyseerr_services,
            configure_jellyfin_libraries=configure_jellyfin_libraries,
            configure_jellyfin_livetv=configure_jellyfin_livetv,
            configure_jellyfin_plugins=configure_jellyfin_plugins,
            configure_jellyfin_playback=configure_jellyfin_playback,
            configure_jellyfin_home_rails=configure_jellyfin_home_rails,
            configure_auto_collections=configure_auto_collections,
            configure_disk_guardrails=configure_disk_guardrails,
            configure_jellyfin_prewarm=configure_jellyfin_prewarm,
            configure_media_hygiene=configure_media_hygiene,
            configure_maintainerr_policy=configure_maintainerr_policy,
            configure_maintainerr_integrations=configure_maintainerr_integrations,
            jellyfin_livetv_tuners=len(jellyfin_livetv_model.tuners),
            jellyfin_livetv_guides=len(jellyfin_livetv_model.guides),
            fully_preconfigured=fully_preconfigured,
            trigger_sync=trigger_sync,
        )
        return BootstrapRuntimeBuildResult(cfg=cfg, runtime=runtime, plan=plan)
