"""Build typed bootstrap runtime state from CLI args and config."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .bootstrap_runner_service import BootstrapRuntime
from .config_models import (
    ArrDiscoveryListsConfig,
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    JellyfinLibrariesConfig,
    JellyfinPlaybackConfig,
    JellyfinPluginsConfig,
    JellyfinPrewarmConfig,
    ServarrAppConfig,
    TechnologyBindingsConfig,
)
from .enums import BootstrapMode
from .technology_catalog import default_servarr_catalog

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

    def load_config(self, config_path: str) -> dict[str, Any]:
        return json.loads(Path(config_path).read_text(encoding="utf-8"))

    def build_from_cli(self, args: BootstrapCliArgs) -> BootstrapRuntimeBuildResult:
        return self.build(args, self.load_config(args.config_path))

    def build(self, args: BootstrapCliArgs, cfg: dict[str, Any]) -> BootstrapRuntimeBuildResult:
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

        download_clients_cfg = cfg.get("download_clients") or {}
        media_server_cfg = cfg.get("media_server") or {}

        default_media_server_operation_plans = self.deps.load_bootstrap_default_json(
            "media_server_operation_plans.json",
            {},
        )

        adapter_hooks_cfg = self.deps.deep_merge_objects(
            self.deps.load_bootstrap_default_json(
                "adapter_hooks.json",
                {},
            ),
            {"media_server_operation_plans": default_media_server_operation_plans},
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
        default_binding_cfg = (adapter_hooks_cfg or {}).get("default_bindings") or {}
        bindings_cfg = TechnologyBindingsConfig.from_dict(
            cfg.get("technology_bindings") or {},
            defaults=default_binding_cfg if isinstance(default_binding_cfg, dict) else {},
        )
        legacy_binding_defaults = TechnologyBindingsConfig.from_dict({})

        raw_aliases = (adapter_hooks_cfg or {}).get("technology_aliases") or {}
        technology_aliases: dict[str, str] = {}
        if isinstance(raw_aliases, dict):
            for source, target in raw_aliases.items():
                src = str(source or "").strip().lower()
                dst = str(target or "").strip().lower()
                if src and dst:
                    technology_aliases[src] = dst

        def _canonical_tech_key(value: str, fallback: str) -> str:
            token = str(value or "").strip().lower() or str(fallback or "").strip().lower()
            if not token:
                return ""
            return technology_aliases.get(token, token)

        def _hook_keys(hook_key: str) -> list[str]:
            hook_map = (adapter_hooks_cfg or {}).get(hook_key) or {}
            if not isinstance(hook_map, dict):
                return []
            keys: list[str] = []
            for key in hook_map.keys():
                canonical = _canonical_tech_key(str(key), "")
                if canonical and canonical not in keys:
                    keys.append(canonical)
            return keys

        def _first_configured_key(candidates: list[str]) -> str:
            for key in candidates:
                if isinstance(download_clients_cfg.get(key), dict):
                    return key
            return ""

        def _resolve_client_cfg(
            requested_key: str,
            fallback_key: str,
        ) -> tuple[str, dict[str, Any]]:
            req = _canonical_tech_key(requested_key, fallback_key)
            fallback = _canonical_tech_key(fallback_key, req)
            candidates = [req]
            if fallback and fallback not in candidates:
                candidates.append(fallback)
            for key in candidates:
                selected = download_clients_cfg.get(key)
                if isinstance(selected, dict):
                    return key, selected
            return req, {}

        configured_download_client_keys = [
            str(key).strip().lower()
            for key, value in (download_clients_cfg or {}).items()
            if isinstance(value, dict) and str(key).strip()
        ]
        hook_download_client_keys = _hook_keys("download_client_adapter_classes")
        fallback_torrent = _canonical_tech_key(
            bindings_cfg.torrent_client,
            legacy_binding_defaults.torrent_client,
        )
        fallback_usenet = _canonical_tech_key(
            bindings_cfg.usenet_client,
            legacy_binding_defaults.usenet_client,
        )
        torrent_default_key = _first_configured_key(
            [
                _canonical_tech_key(bindings_cfg.torrent_client, ""),
                *hook_download_client_keys,
                *configured_download_client_keys,
            ]
        ) or _first_configured_key(
            [
                fallback_torrent,
                fallback_usenet,
                *hook_download_client_keys,
                *configured_download_client_keys,
            ]
        ) or fallback_torrent
        usenet_default_key = _first_configured_key(
            [
                _canonical_tech_key(bindings_cfg.usenet_client, ""),
                *hook_download_client_keys,
                *configured_download_client_keys,
            ]
        ) or _first_configured_key(
            [
                fallback_usenet,
                fallback_torrent,
                *hook_download_client_keys,
                *configured_download_client_keys,
            ]
        ) or fallback_usenet

        torrent_client_key, qbit_cfg = _resolve_client_cfg(
            bindings_cfg.torrent_client,
            torrent_default_key,
        )
        usenet_client_key, sab_cfg = _resolve_client_cfg(
            bindings_cfg.usenet_client,
            usenet_default_key,
        )

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

        jellyseerr_cfg = cfg.get("jellyseerr") or {}
        homepage_cfg = cfg.get("homepage") or {}
        bazarr_cfg = cfg.get("bazarr") or {}
        jellyfin_libraries_model = JellyfinLibrariesConfig.from_dict(
            cfg.get("jellyfin_libraries") or {}
        )
        jellyfin_livetv_cfg = cfg.get("jellyfin_livetv") or {}
        jellyfin_plugins_model = JellyfinPluginsConfig.from_dict(cfg.get("jellyfin_plugins") or {})
        jellyfin_playback_model = JellyfinPlaybackConfig.from_dict(
            cfg.get("jellyfin_playback") or {}
        )
        jellyfin_home_rails_cfg = cfg.get("jellyfin_home_rails") or {}
        jellyfin_auto_collections_cfg = cfg.get("jellyfin_auto_collections") or {}
        jellyfin_prewarm_model = JellyfinPrewarmConfig.from_dict(cfg.get("jellyfin_prewarm") or {})
        disk_guardrails_cfg = cfg.get("disk_guardrails") or {}
        media_hygiene_cfg = cfg.get("media_hygiene") or {}
        maintainerr_cfg = cfg.get("maintainerr") or {}

        hook_media_server_keys = _hook_keys("media_server_adapter_classes")
        media_server_default_key = (
            _canonical_tech_key(bindings_cfg.media_server, "")
            or (hook_media_server_keys[0] if hook_media_server_keys else "")
            or _canonical_tech_key(bindings_cfg.media_server, legacy_binding_defaults.media_server)
            or legacy_binding_defaults.media_server
        )
        media_server_backend = _canonical_tech_key(
            str(media_server_cfg.get("backend") or bindings_cfg.media_server),
            media_server_default_key,
        )

        app_auth_cfg = cfg.get("app_auth") or {}
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

        configure_jellyseerr_services = self.deps.bool_cfg(jellyseerr_cfg, "enabled", False)
        jellyseerr_required = self.deps.bool_cfg(jellyseerr_cfg, "required", False)

        configure_homepage_services = self.deps.bool_cfg(homepage_cfg, "enabled", False) or bool(
            self.deps.coerce_list(homepage_cfg.get("hosts"))
        )
        homepage_required = self.deps.bool_cfg(homepage_cfg, "required", False)

        configure_bazarr_integration = self.deps.bool_cfg(bazarr_cfg, "enabled", False)
        bazarr_required = self.deps.bool_cfg(bazarr_cfg, "required", False)

        configure_jellyfin_libraries = jellyfin_libraries_model.enabled
        jellyfin_libraries_required = jellyfin_libraries_model.required

        configure_jellyfin_livetv = self.deps.bool_cfg(jellyfin_livetv_cfg, "enabled", False)
        jellyfin_livetv_required = self.deps.bool_cfg(jellyfin_livetv_cfg, "required", False)

        configure_jellyfin_plugins = jellyfin_plugins_model.enabled
        jellyfin_plugins_required = jellyfin_plugins_model.required

        configure_jellyfin_playback = jellyfin_playback_model.enabled
        jellyfin_playback_required = jellyfin_playback_model.required

        configure_jellyfin_home_rails = self.deps.bool_cfg(
            jellyfin_home_rails_cfg,
            "enabled",
            False,
        ) or self.deps.bool_cfg(
            jellyfin_home_rails_cfg,
            "cleanup_collections_when_disabled",
            False,
        )
        jellyfin_home_rails_required = self.deps.bool_cfg(
            jellyfin_home_rails_cfg,
            "required",
            False,
        )

        configure_auto_collections = self.deps.bool_cfg(
            jellyfin_auto_collections_cfg,
            "enabled",
            False,
        )
        auto_collections_required = self.deps.bool_cfg(
            jellyfin_auto_collections_cfg,
            "required",
            False,
        )

        configure_disk_guardrails = self.deps.bool_cfg(disk_guardrails_cfg, "enabled", False)
        disk_guardrails_required = self.deps.bool_cfg(disk_guardrails_cfg, "required", False)

        configure_jellyfin_prewarm = jellyfin_prewarm_model.enabled
        jellyfin_prewarm_required = jellyfin_prewarm_model.required

        configure_media_hygiene = self.deps.bool_cfg(media_hygiene_cfg, "enabled", False)
        media_hygiene_required = self.deps.bool_cfg(media_hygiene_cfg, "required", False)

        configure_maintainerr_policy = self.deps.bool_cfg(maintainerr_cfg, "enabled", False)
        maintainerr_required = self.deps.bool_cfg(maintainerr_cfg, "required", False)

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

        torrent_username_env = str(qbit_cfg.get("username_env", "QBITTORRENT_USERNAME")).strip()
        torrent_password_env = str(qbit_cfg.get("password_env", "QBITTORRENT_PASSWORD")).strip()
        qb_user = (
            os.environ.get(torrent_username_env)
            or os.environ.get("QBITTORRENT_USERNAME")
            or os.environ.get("STACK_ADMIN_USERNAME")
            or "mediaadmin"
        )
        qb_pass = (
            os.environ.get(torrent_password_env)
            or os.environ.get("QBITTORRENT_PASSWORD")
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
            app_auth_enabled=self.deps.bool_cfg(app_auth_cfg, "enabled", False),
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
            jellyfin_livetv_tuners=len(self.deps.coerce_list(jellyfin_livetv_cfg.get("tuners"))),
            jellyfin_livetv_guides=len(self.deps.coerce_list(jellyfin_livetv_cfg.get("guides"))),
            fully_preconfigured=fully_preconfigured,
            trigger_sync=trigger_sync,
        )
        return BootstrapRuntimeBuildResult(cfg=cfg, runtime=runtime, plan=plan)
