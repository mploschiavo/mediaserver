"""Runtime builder for bootstrap orchestration."""

from __future__ import annotations

import importlib as _importlib
import os
import sys
from dataclasses import dataclass
from typing import Any

from media_stack.services.apps.download_clients.config_models import (
    DownloadClientsConfig,
    TechnologyBindingsConfig,
)
from media_stack.services.apps.download_clients.config_resolver import (
    resolve_download_client_configs,
)
from media_stack.services.apps.integrations.config_models import AppAuthConfig
from media_stack.services.apps.integrations.config_resolver import (
    resolve_integration_configs,
)
from media_stack.services.apps.servarr.config_models import (
    ArrDiscoveryListsConfig,
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from media_stack.services.enums import BootstrapMode
from media_stack.services.plugin_manifest_loader import (
    build_adapter_hook_defaults,
    collect_capability_defaults,
    load_plugin_manifests,
)
from media_stack.services.runtime_models import ControllerRuntime
from media_stack.services.technology_catalog import build_servarr_catalog_from_manifests
from media_stack.services.top_level_config_model import TopLevelBootstrapConfig
from media_stack.domain.runtime_factory.models import (
    ControllerCliArgs,
    ControllerRuntimeBuildResult,
    ControllerRuntimeFactoryDependencies,
)
from media_stack.domain.runtime_factory.plan_builder import build_plan_summary
from .binding_resolver import RuntimeBindingResolver


class RuntimeBuilder:
    """Module-level helpers folded into a class per ADR-0012.

    Holds the dynamic-import lookups for the active media-server and
    indexer-manager technology bindings, plus the public proxy helpers
    (``resolve_jellyfin_configs`` etc.) that dispatch into the resolved
    modules. Tests `mock.patch` the public names at the module level,
    and ``ControllerRuntimeBuilder.build`` re-fetches them through
    ``sys.modules[__name__]`` so the patches take effect.
    """

    def _load_media_server_config_resolver(self):
        """Dynamically load the media server config resolver from the active technology binding."""
        from media_stack.core.service_registry.registry import SERVICES
        for svc in SERVICES:
            if svc.category != "media":
                continue
            try:
                return _importlib.import_module(f"media_stack.services.apps.{svc.id}.config_resolver")
            except (ImportError, ModuleNotFoundError):
                continue
        return None

    def _load_indexer_manager_key_reader(self):
        """Dynamically load the indexer manager API key reader from the active technology binding."""
        from media_stack.core.service_registry.registry import SERVICES
        for svc in SERVICES:
            if not svc.indexer_path:
                continue
            try:
                return _importlib.import_module(f"media_stack.services.apps.{svc.id}.api_key_reader")
            except (ImportError, ModuleNotFoundError):
                continue
        return None

    def resolve_jellyfin_configs(self, *args, **kwargs):
        return self._load_media_server_config_resolver().resolve_jellyfin_configs(*args, **kwargs)

    def populate_prowlarr_service_dicts(self, *args, **kwargs):
        return self._load_indexer_manager_key_reader().populate_prowlarr_service_dicts(*args, **kwargs)

    def read_prowlarr_api_key(self, *args, **kwargs):
        return self._load_indexer_manager_key_reader().read_prowlarr_api_key(*args, **kwargs)

    def resolve_prowlarr_wiring(self, *args, **kwargs):
        return self._load_indexer_manager_key_reader().resolve_prowlarr_wiring(*args, **kwargs)


_INSTANCE = RuntimeBuilder()
_load_media_server_config_resolver = _INSTANCE._load_media_server_config_resolver
_load_indexer_manager_key_reader = _INSTANCE._load_indexer_manager_key_reader
resolve_jellyfin_configs = _INSTANCE.resolve_jellyfin_configs
populate_prowlarr_service_dicts = _INSTANCE.populate_prowlarr_service_dicts
read_prowlarr_api_key = _INSTANCE.read_prowlarr_api_key
resolve_prowlarr_wiring = _INSTANCE.resolve_prowlarr_wiring


@dataclass
class ControllerRuntimeBuilder:
    deps: ControllerRuntimeFactoryDependencies

    def _validate_adapter_registration_overrides(self, adapter_hooks_cfg: dict[str, Any]) -> None:
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

    def _resolve_optional_env_value(self, client_cfg: dict[str, Any], env_key_name: str) -> str:
        env_name = str(client_cfg.get(env_key_name) or "").strip()
        if not env_name:
            return ""
        return str(os.environ.get(env_name) or "").strip()

    def _resolve_required_env_value(
        self,
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
                "media_server_event_plans": default_media_server_operation_plans,
                "runner_event_plans": default_runner_operation_plans,
            },
        )

        raw_cfg_hooks = cfg.get("adapter_hooks") or {}
        if not isinstance(raw_cfg_hooks, dict):
            raise ValueError("adapter_hooks must be an object/map.")
        self._validate_adapter_registration_overrides(raw_cfg_hooks)

        allowed_runtime_overrides: dict[str, Any] = {}
        for key in (
            "event_handlers",
            "runner_event_plans",
            "runner_operation_plans",
            "media_server_event_plans",
            "media_server_operation_plans",
        ):
            value = raw_cfg_hooks.get(key)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"adapter_hooks.{key} must be an object/map.")
            allowed_runtime_overrides[key] = value

        legacy_operation_handlers = raw_cfg_hooks.get("operation_handlers")
        if legacy_operation_handlers is not None:
            if not isinstance(legacy_operation_handlers, dict):
                raise ValueError("adapter_hooks.operation_handlers must be an object/map.")
            event_overrides = allowed_runtime_overrides.setdefault("event_handlers", {})
            if not isinstance(event_overrides, dict):
                raise ValueError(
                    "adapter_hooks.event_handlers.RUN must be an object/map when provided."
                )
            run_event = event_overrides.setdefault("RUN", {})
            if not isinstance(run_event, dict):
                raise ValueError(
                    "adapter_hooks.event_handlers.RUN must be an object/map when provided."
                )
            run_event.update(legacy_operation_handlers)

        adapter_hooks_cfg = self.deps.deep_merge_objects(merged_defaults, allowed_runtime_overrides)
        adapter_hooks_cfg = self.deps.deep_merge_objects(
            adapter_hooks_cfg,
            {
                "media_server_event_plans": media_server_cfg.get("operation_plans") or {},
                "media_server_operation_plans": media_server_cfg.get("operation_plans") or {},
            },
        )
        event_handlers_cfg = adapter_hooks_cfg.get("event_handlers")
        if isinstance(event_handlers_cfg, dict):
            run_handlers = event_handlers_cfg.get("RUN")
            if isinstance(run_handlers, dict):
                adapter_hooks_cfg["operation_handlers"] = dict(run_handlers)
        return adapter_hooks_cfg

    def build(self, args: ControllerCliArgs, cfg: dict[str, Any]) -> ControllerRuntimeBuildResult:
        # Dispatch public proxy helpers through ``sys.modules[__name__]``
        # so tests that ``mock.patch`` the module-level names take effect.
        _self_mod = sys.modules[__name__]

        cfg = TopLevelBootstrapConfig.from_dict(cfg).to_dict()
        prowlarr_wiring = _self_mod.resolve_prowlarr_wiring(cfg=cfg)
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
        torrent_client_cfg = dict(binding_resolution.torrent_client_cfg)
        usenet_client_cfg = dict(binding_resolution.usenet_client_cfg)
        if torrent_client_key:
            torrent_client_cfg.setdefault("_technology_key", torrent_client_key)
        if usenet_client_key:
            usenet_client_cfg.setdefault("_technology_key", usenet_client_key)

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

        # ── Data-driven config resolution ────────────────────────────
        # Each app package provides a config resolver that reads its
        # config sections and returns models + feature flags.
        integration_result = resolve_integration_configs(cfg)
        jellyfin_result = _self_mod.resolve_jellyfin_configs(cfg)
        download_client_result = resolve_download_client_configs(cfg)

        # Merge all feature flags from data-driven resolvers
        service_feature_flags: dict[str, bool] = {}
        service_feature_flags.update(integration_result.feature_flags)
        service_feature_flags.update(jellyfin_result.feature_flags)
        service_feature_flags.update(download_client_result.feature_flags)

        media_server_backend = binding_resolution.media_server_backend
        request_manager_backend = binding_resolution.request_manager_key

        app_auth_model = AppAuthConfig.from_dict(cfg.get("app_auth") or {})
        app_auth_cfg = dict(app_auth_model.raw)
        fully_preconfigured = self.deps.env_truthy(
            "FULLY_PRECONFIGURED", False
        ) or self.deps.env_truthy("APPLY_INITIAL_PREFERENCES", False)
        if fully_preconfigured and not app_auth_cfg:
            include_apps = [str(app.name or app.implementation).strip() for app in arr_apps]
            include_apps = [name for name in include_apps if name]
            if prowlarr_wiring.include_in_app_auth:
                indexer_mgr_name = prowlarr_wiring.display_name
                if indexer_mgr_name not in include_apps:
                    include_apps.append(indexer_mgr_name)
            app_auth_cfg = {
                "enabled": True,
                "method": "Forms",
                "required": "Enabled",
                "username_env": "STACK_ADMIN_USERNAME",
                "password_env": "STACK_ADMIN_PASSWORD",
                "include": include_apps,
            }
        # When SSO is active, set External auth on arr apps so they trust
        # the reverse proxy. Use ProfileConfig as the source of truth.
        try:
            from media_stack.services.profile_config import get_profile_config
            profile = get_profile_config()
            if profile.is_sso_active and app_auth_cfg.get("enabled"):
                app_auth_cfg["method"] = profile.effective_app_auth_method
                app_auth_cfg["required"] = "DisabledForLocalAddresses"
        except Exception:
            # Fallback: read from cfg dict (backward compat)
            auth_section = cfg.get("auth") or {}
            auth_mode = str(auth_section.get("provider") or auth_section.get("mode") or "").strip().lower()
            if auth_mode in ("authelia", "authentik") and app_auth_cfg.get("enabled"):
                app_auth_cfg["method"] = "External"
                app_auth_cfg["required"] = "DisabledForLocalAddresses"

        app_auth_model = AppAuthConfig.from_dict(app_auth_cfg)

        auto_indexers = bool(
            cfg.get("prowlarr_auto_add_tested_indexers", False) or args.auto_prowlarr_indexers
        )

        configure_torrent_arr_clients = bool(
            torrent_client_key and torrent_client_cfg.get("configure_arr_clients", False)
        )
        configure_usenet_arr_clients = bool(
            usenet_client_key and usenet_client_cfg.get("configure_arr_clients", False)
        )
        sab_remote_path_mappings = (
            self.deps.build_sab_remote_path_mappings(usenet_client_cfg)
            if configure_usenet_arr_clients
            else []
        )

        configure_arr_media_management = arr_media_management_cfg.enabled
        configure_arr_quality_upgrade = arr_quality_upgrade_cfg.enabled
        configure_arr_download_handling = arr_download_handling_cfg.enabled
        configure_arr_discovery_lists = arr_discovery_lists_cfg.enabled
        set_torrent_categories = bool(
            torrent_client_key
            and torrent_client_cfg.get(
                "set_categories", torrent_client_cfg.get("set_categories_in_qbit", False)
            )
        )
        torrent_login_required = bool(
            torrent_client_key and torrent_client_cfg.get("login_required", fully_preconfigured)
        )
        refresh_health_after_setup = bool(cfg.get("refresh_health_after_setup", True))

        trigger_sync = bool(cfg.get("trigger_indexer_sync", True))

        app_keys: dict[str, str] = {}
        skipped_apps: list[str] = []
        if args.mode in (BootstrapMode.FULL, BootstrapMode.MEDIA_HYGIENE):
            # Read all API keys in parallel — each may wait for the
            # service to start, so sequential reads are 180s * N.
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=len(arr_apps) + 1) as pool:
                futures = [pool.submit(self._read_api_key_for_app, args.config_root, app) for app in arr_apps]
                # Also read prowlarr key in parallel
                if args.mode == BootstrapMode.FULL and prowlarr_wiring.url:
                    prowlarr_future = pool.submit(self._read_prowlarr_key, args.config_root)
                else:
                    prowlarr_future = None

                for future in as_completed(futures):
                    app_dir, impl, key, err = future.result()
                    if key:
                        app_keys[impl] = key
                        app_keys[impl.lower()] = key
                    else:
                        print(
                            f"[WARN] {app_dir}: API key unavailable, skipping "
                            f"({err}). Service will be configured on next reconcile.",
                            file=sys.stderr,
                        )
                        skipped_apps.append(app_dir)

                if prowlarr_future is not None:
                    prowlarr_wiring.key, prowlarr_skipped = prowlarr_future.result()
                    skipped_apps.extend(prowlarr_skipped)

        if skipped_apps:
            print(
                f"[WARN] Bootstrap continuing without: {', '.join(skipped_apps)}. "
                "These services will be configured when their API keys become available.",
                file=sys.stderr,
            )

        torrent_user = self._resolve_optional_env_value(torrent_client_cfg, "username_env")
        torrent_pass = self._resolve_optional_env_value(torrent_client_cfg, "password_env")
        if torrent_client_key and torrent_login_required and (not torrent_user or not torrent_pass):
            missing = []
            if not torrent_user:
                missing.append("username_env")
            if not torrent_pass:
                missing.append("password_env")
            raise ValueError(
                "Missing required torrent client credential binding(s): "
                f"{', '.join(missing)} for technology '{torrent_client_key}'."
            )

        usenet_username = self._resolve_optional_env_value(usenet_client_cfg, "username_env")
        usenet_password = self._resolve_optional_env_value(usenet_client_cfg, "password_env")
        usenet_login_required = bool(
            usenet_client_key and usenet_client_cfg.get("login_required", False)
        )
        if usenet_client_key and usenet_login_required and (
            not usenet_username or not usenet_password
        ):
            missing = []
            if not usenet_username:
                missing.append("username_env")
            if not usenet_password:
                missing.append("password_env")
            raise ValueError(
                "Missing required usenet client credential binding(s): "
                f"{', '.join(missing)} for technology '{usenet_client_key}'."
            )

        # ── Build service dicts dynamically ──────────────────────────
        # Populate from binding resolution rather than hardcoding service names.
        service_urls: dict[str, str] = {}
        service_keys: dict[str, str] = {**app_keys}
        service_configs: dict[str, dict[str, Any]] = {}
        service_credentials: dict[str, dict[str, str]] = {}
        service_data: dict[str, Any] = {
            "sab_remote_path_mappings": sab_remote_path_mappings,
        }

        # Prowlarr wiring is handled by the app layer
        _self_mod.populate_prowlarr_service_dicts(
            prowlarr_wiring,
            service_urls=service_urls,
            service_keys=service_keys,
            service_data=service_data,
        )

        if torrent_client_key:
            service_configs[torrent_client_key] = torrent_client_cfg
            service_credentials[torrent_client_key] = {
                "user": torrent_user,
                "pass": torrent_pass,
            }
        if usenet_client_key:
            service_configs[usenet_client_key] = usenet_client_cfg
            service_credentials[usenet_client_key] = {
                "user": usenet_username,
                "pass": usenet_password,
            }

        # ── Merge non-data-driven feature flags ──────────────────────
        service_feature_flags.update({
            "configure_qbit_arr_clients": configure_torrent_arr_clients,
            "configure_sab_arr_clients": configure_usenet_arr_clients,
            "configure_arr_media_management": configure_arr_media_management,
            "configure_arr_download_handling": configure_arr_download_handling,
            "configure_arr_quality_upgrade": configure_arr_quality_upgrade,
            "configure_arr_discovery_lists": configure_arr_discovery_lists,
            "set_qbit_categories": set_torrent_categories,
            "qbit_login_required": torrent_login_required,
            "refresh_health_after_setup": refresh_health_after_setup,
        })

        runtime = ControllerRuntime(
            mode=args.mode,
            cfg=cfg,
            config_root=args.config_root,
            wait_timeout=args.wait_timeout,
            arr_apps_raw=arr_apps_raw,
            arr_apps=arr_apps,
            app_keys=app_keys,
            torrent_client_key=torrent_client_key,
            usenet_client_key=usenet_client_key,
            arr_media_management_cfg=arr_media_management_cfg,
            arr_download_handling_cfg=arr_download_handling_cfg,
            arr_quality_upgrade_cfg=arr_quality_upgrade_cfg,
            app_auth_cfg=app_auth_cfg,
            adapter_hooks_cfg=adapter_hooks_cfg,
            auto_indexers=auto_indexers,
            trigger_sync=trigger_sync,
            fully_preconfigured=fully_preconfigured,
            service_urls=service_urls,
            service_keys=service_keys,
            service_configs=service_configs,
            service_credentials=service_credentials,
            service_data=service_data,
            media_server_backend=media_server_backend,
            request_manager_backend=request_manager_backend,
            **service_feature_flags,
        )

        plan = build_plan_summary(runtime)
        return ControllerRuntimeBuildResult(cfg=cfg, runtime=runtime, plan=plan)

    def _read_api_key_for_app(self, config_root, app):
        """Worker for parallel arr-app API-key reads (was nested ``_read_key``)."""
        app_dir = app.implementation.lower()
        try:
            key = self.deps.read_api_key(config_root, app_dir)
            return app_dir, app.implementation, key, None
        except RuntimeError as exc:
            return app_dir, app.implementation, None, exc

    def _read_prowlarr_key(self, config_root):
        """Worker for parallel prowlarr API-key read (was nested ``_read_prowlarr``)."""
        return sys.modules[__name__].read_prowlarr_api_key(
            config_root=config_root,
            read_api_key=self.deps.read_api_key,
        )
