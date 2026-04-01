"""Runtime builder for bootstrap orchestration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

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
    MaintainerrConfig,
    MediaHygieneConfig,
    ServarrAppConfig,
    TechnologyBindingsConfig,
)
from ..enums import BootstrapMode
from ..plugin_manifest_loader import (
    build_adapter_hook_defaults,
    collect_capability_defaults,
    load_plugin_manifests,
)
from ..runtime_models import BootstrapRuntime
from ..technology_catalog import build_servarr_catalog_from_manifests
from ..top_level_config_model import TopLevelBootstrapConfig
from .binding_resolver import RuntimeBindingResolver
from .models import (
    BootstrapCliArgs,
    BootstrapRuntimeBuildResult,
    BootstrapRuntimeFactoryDependencies,
)
from .plan_builder import build_plan_summary


@dataclass
class BootstrapRuntimeBuilder:
    deps: BootstrapRuntimeFactoryDependencies

    @staticmethod
    def _validate_adapter_registration_overrides(adapter_hooks_cfg: dict[str, Any]) -> None:
        disallowed_override_keys = (
            "technology_aliases",
            "adapter_classes",
            "download_client_adapter_classes",
            "media_server_adapter_classes",
            "before_common_steps",
            "app_service_classes",
            "service_technology_map",
        )
        for key in disallowed_override_keys:
            value = adapter_hooks_cfg.get(key)
            if isinstance(value, dict) and value:
                raise ValueError(
                    "Adapter/service registration overrides are no longer supported in "
                    "bootstrap config. Move registrations into per-technology plugin manifests "
                    f"(disallowed key: adapter_hooks.{key})."
                )
            if value not in (None, {}):
                raise ValueError(f"adapter_hooks.{key} must be an object if provided.")

    @staticmethod
    def _resolve_optional_env_value(client_cfg: dict[str, Any], env_key_name: str) -> str:
        env_name = str(client_cfg.get(env_key_name) or "").strip()
        if not env_name:
            return ""
        return str(os.environ.get(env_name) or "").strip()

    @staticmethod
    def _resolve_required_env_value(
        client_cfg: dict[str, Any],
        *,
        env_key_name: str,
        binding_label: str,
    ) -> str:
        env_name = str(client_cfg.get(env_key_name) or "").strip()
        if not env_name:
            raise ValueError(
                f"Missing required binding for {binding_label}: "
                f"download_clients.{binding_label}.{env_key_name}"
            )
        value = str(os.environ.get(env_name) or "").strip()
        if not value:
            raise ValueError(
                f"Missing required environment variable for {binding_label}: {env_name}"
            )
        return value

    def _build_adapter_hooks_cfg(
        self,
        *,
        cfg: dict[str, Any],
        media_server_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        manifests = load_plugin_manifests()
        default_media_server_operation_plans = self.deps.load_bootstrap_default_json(
            "media_server_operation_plans.json",
            {},
        )
        default_runner_operation_plans = self.deps.load_bootstrap_default_json(
            "runner_operation_plans.json",
            {},
        )
        merged_defaults = self.deps.deep_merge_objects(
            build_adapter_hook_defaults(manifests).to_dict(),
            {
                "media_server_operation_plans": default_media_server_operation_plans,
                "runner_operation_plans": default_runner_operation_plans,
            },
        )

        raw_cfg_hooks = cfg.get("adapter_hooks") or {}
        if not isinstance(raw_cfg_hooks, dict):
            raise ValueError("adapter_hooks must be an object/map.")
        self._validate_adapter_registration_overrides(raw_cfg_hooks)

        allowed_runtime_overrides: dict[str, Any] = {}
        for key in ("operation_handlers", "runner_operation_plans", "media_server_operation_plans"):
            value = raw_cfg_hooks.get(key)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"adapter_hooks.{key} must be an object/map.")
            allowed_runtime_overrides[key] = value

        adapter_hooks_cfg = self.deps.deep_merge_objects(merged_defaults, allowed_runtime_overrides)
        adapter_hooks_cfg = self.deps.deep_merge_objects(
            adapter_hooks_cfg,
            {
                "media_server_operation_plans": media_server_cfg.get("operation_plans") or {},
            },
        )
        return adapter_hooks_cfg

    def build(self, args: BootstrapCliArgs, cfg: dict[str, Any]) -> BootstrapRuntimeBuildResult:
        cfg = TopLevelBootstrapConfig.from_dict(cfg).to_dict()
        prowlarr_url = str(cfg.get("prowlarr_url") or "").strip().rstrip("/")
        arr_apps_raw = cfg.get("arr_apps", [])

        manifests = load_plugin_manifests()
        catalog = build_servarr_catalog_from_manifests(manifests)
        manifest_capability_defaults = collect_capability_defaults(manifests)
        arr_default_capability_defaults = self.deps.load_bootstrap_default_json(
            "app_capability_defaults.json",
            {},
        )
        arr_app_capability_defaults = catalog.expand_capability_defaults(
            self.deps.deep_merge_objects(
                self.deps.deep_merge_objects(
                    arr_default_capability_defaults,
                    manifest_capability_defaults,
                ),
                cfg.get("app_capability_defaults") or {},
            )
        )
        arr_apps = ServarrAppConfig.from_list(
            arr_apps_raw,
            capability_defaults=arr_app_capability_defaults,
        )

        download_clients_model = DownloadClientsConfig.from_dict(cfg.get("download_clients") or {})
        media_server_cfg = cfg.get("media_server") or {}
        adapter_hooks_cfg = self._build_adapter_hooks_cfg(
            cfg=cfg, media_server_cfg=media_server_cfg
        )

        bindings_cfg = TechnologyBindingsConfig.from_dict(cfg.get("technology_bindings") or {})
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

        qb_user = ""
        qb_pass = ""
        if torrent_client_key == "qbittorrent":
            qb_user = self._resolve_required_env_value(
                qbit_cfg,
                env_key_name="username_env",
                binding_label=torrent_client_key,
            )
            qb_pass = self._resolve_required_env_value(
                qbit_cfg,
                env_key_name="password_env",
                binding_label=torrent_client_key,
            )

        sab_username = self._resolve_optional_env_value(sab_cfg, "username_env")
        sab_password = self._resolve_optional_env_value(sab_cfg, "password_env")

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

        plan = build_plan_summary(runtime)
        return BootstrapRuntimeBuildResult(cfg=cfg, runtime=runtime, plan=plan)
