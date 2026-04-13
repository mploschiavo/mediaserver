"""Loading and caching of the bootstrap profile catalog YAML."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.auth.provider_registry import merge_auth_provider_defaults
from media_stack.core.controller_profile.normalizers import (
    _as_bool_with_tokens,
    _coerce_url_list,
    _normalize_alias_dict,
    _normalize_app_token,
    _normalize_chaos_actions,
    _normalize_string_list,
    _normalize_string_list_allow_empty,
    _to_positive_int,
)

_DEFAULT_PROFILE_CATALOG_PATH = (
    Path(__file__).resolve().parents[4] / "contracts" / "media-stack.catalog.yaml"
)



class CatalogLoaderService:
    @staticmethod
    def _resolve_catalog_path(path: Path | None = None) -> Path:
        if path is not None:
            return path.expanduser()
        env_path = str(os.environ.get("BOOTSTRAP_PROFILE_CATALOG_FILE") or "").strip()
        if env_path:
            return Path(env_path).expanduser()
        return _DEFAULT_PROFILE_CATALOG_PATH
    
    
    @staticmethod
    @lru_cache(maxsize=8)
    def _load_bootstrap_profile_catalog_cached(path_token: str) -> Any:
        from media_stack.core.controller_profile.models import ControllerProfileCatalog
    
        path = Path(path_token)
        if not path.exists():
            # Try image-embedded path
            path = Path("/opt/media-stack/contracts/media-stack.catalog.yaml")
        if not path.exists():
            raise ValueError(f"Bootstrap profile catalog file not found: {path}")
    
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("Bootstrap profile catalog must contain an object at root")
    
        # Enrich apps.keys from service registry if available
        try:
            from media_stack.api.services.registry import SERVICES
            registry_app_keys = [s.id for s in SERVICES]
            apps_section = payload.setdefault("apps", {})
            existing_keys = set(apps_section.get("keys", []))
            for key in registry_app_keys:
                if key not in existing_keys:
                    apps_section.setdefault("keys", []).append(key)
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
    
        deployment_aliases = _normalize_alias_dict(
            payload.get("deployment_aliases"),
            field_name="deployment_aliases",
        )
        purpose_values = _normalize_string_list(
            payload.get("purpose_values"),
            field_name="purpose_values",
        )
        route_strategy_aliases = _normalize_alias_dict(
            payload.get("route_strategy_aliases"),
            field_name="route_strategy_aliases",
        )
        auth_providers = _normalize_string_list(
            payload.get("auth_providers"),
            field_name="auth_providers",
        )
        auth_disabled_provider = str(payload.get("auth_disabled_provider") or "").strip().lower()
        if not auth_disabled_provider:
            auth_disabled_provider = auth_providers[0]
        if auth_disabled_provider not in set(auth_providers):
            raise ValueError(
                "auth_disabled_provider must be one of auth_providers "
                f"(got '{auth_disabled_provider}')"
            )
        raw_auth_defaults = payload.get("auth_provider_middleware_defaults")
        normalized_auth_defaults: dict[str, str] = {}
        if raw_auth_defaults is not None:
            if not isinstance(raw_auth_defaults, dict):
                raise ValueError("auth_provider_middleware_defaults must be an object")
            for raw_provider, raw_middleware in raw_auth_defaults.items():
                provider_key = str(raw_provider or "").strip().lower()
                if not provider_key:
                    continue
                if provider_key not in set(auth_providers):
                    raise ValueError(
                        "auth_provider_middleware_defaults contains unknown provider "
                        f"'{provider_key}'"
                    )
                normalized_auth_defaults[provider_key] = str(raw_middleware or "").strip()
        auth_provider_middleware_defaults = merge_auth_provider_defaults(
            provider_keys=auth_providers,
            catalog_defaults=normalized_auth_defaults,
        )
    
        apps_payload = payload.get("apps")
        if not isinstance(apps_payload, dict):
            raise ValueError("apps must be an object in bootstrap profile catalog")
        app_keys = _normalize_string_list(apps_payload.get("keys"), field_name="apps.keys")
        app_key_set = set(app_keys)
    
        raw_aliases = apps_payload.get("aliases")
        app_aliases: dict[str, str] = {}
        if raw_aliases is not None:
            if not isinstance(raw_aliases, dict):
                raise ValueError("apps.aliases must be an object when provided")
            for raw_key, raw_value in raw_aliases.items():
                alias_key = _normalize_app_token(raw_key)
                alias_target = _normalize_app_token(raw_value)
                if not alias_key or not alias_target:
                    continue
                if alias_target not in app_key_set:
                    raise ValueError(
                        f"apps.aliases contains unknown target '{raw_value}'. "
                        "Targets must be present in apps.keys."
                    )
                app_aliases[alias_key] = alias_target
    
        install_profiles_payload = payload.get("install_profiles")
        if not isinstance(install_profiles_payload, dict) or not install_profiles_payload:
            raise ValueError("install_profiles must be a non-empty object")
    
        install_profiles: dict[str, tuple[str, ...]] = {}
        for raw_profile_name, raw_profile_spec in install_profiles_payload.items():
            profile_name = str(raw_profile_name or "").strip().lower()
            if not profile_name:
                continue
    
            enabled_raw: Any
            if isinstance(raw_profile_spec, dict):
                enabled_raw = raw_profile_spec.get("enabled_apps")
            else:
                enabled_raw = raw_profile_spec
    
            if isinstance(enabled_raw, str) and enabled_raw.strip() == "*":
                install_profiles[profile_name] = app_keys
                continue
    
            if not isinstance(enabled_raw, list):
                raise ValueError(f"install_profiles.{profile_name}.enabled_apps must be a list or '*'")
    
            enabled_list: list[str] = []
            seen: set[str] = set()
            for raw_app in enabled_raw:
                app_name = _normalize_app_token(raw_app)
                if not app_name:
                    continue
                app_name = app_aliases.get(app_name, app_name)
                if app_name not in app_key_set:
                    raise ValueError(
                        f"install_profiles.{profile_name}.enabled_apps contains unknown app '{raw_app}'"
                    )
                if app_name in seen:
                    continue
                seen.add(app_name)
                enabled_list.append(app_name)
            install_profiles[profile_name] = tuple(enabled_list)
    
        boolean_tokens = payload.get("boolean_tokens")
        if not isinstance(boolean_tokens, dict):
            raise ValueError("boolean_tokens must be an object")
        true_tokens_raw = boolean_tokens.get("true")
        if true_tokens_raw is None:
            true_tokens_raw = boolean_tokens.get(True)
        false_tokens_raw = boolean_tokens.get("false")
        if false_tokens_raw is None:
            false_tokens_raw = boolean_tokens.get(False)
        bool_true_tokens = _normalize_string_list(
            true_tokens_raw,
            field_name="boolean_tokens.true",
        )
        bool_false_tokens = _normalize_string_list(
            false_tokens_raw,
            field_name="boolean_tokens.false",
        )
    
        chaos_defaults_payload = payload.get("chaos_defaults")
        if chaos_defaults_payload is None:
            chaos_defaults_payload = {}
        if not isinstance(chaos_defaults_payload, dict):
            raise ValueError("chaos_defaults must be an object when provided")
        chaos_default_enabled = _as_bool_with_tokens(
            chaos_defaults_payload.get("enabled"),
            default=False,
            true_tokens=bool_true_tokens,
            false_tokens=bool_false_tokens,
        )
        chaos_default_duration_minutes = _to_positive_int(
            chaos_defaults_payload.get("duration_minutes"),
            default=5,
            field_name="chaos_defaults.duration_minutes",
            minimum=1,
            maximum=120,
        )
        chaos_default_interval_seconds = _to_positive_int(
            chaos_defaults_payload.get("interval_seconds"),
            default=60,
            field_name="chaos_defaults.interval_seconds",
            minimum=0,
            maximum=3600,
        )
        chaos_allowed_actions = _normalize_string_list_allow_empty(
            chaos_defaults_payload.get("allowed_actions"),
            field_name="chaos_defaults.allowed_actions",
            default=("restart_container", "pause_container", "network_disconnect"),
        )
        chaos_default_actions = _normalize_chaos_actions(
            chaos_defaults_payload.get("actions"),
            allowed=chaos_allowed_actions,
            default=chaos_allowed_actions,
        )
    
        live_tv_defaults = payload.get("live_tv_defaults")
        if not isinstance(live_tv_defaults, dict):
            raise ValueError("live_tv_defaults must be an object")
        live_tv_tuner_urls = _coerce_url_list(
            live_tv_defaults.get("tuner_urls")
            or live_tv_defaults.get("tuner_url")
            or live_tv_defaults.get("playlists")
        )
        live_tv_guide_urls = _coerce_url_list(
            live_tv_defaults.get("guide_urls")
            or live_tv_defaults.get("guide_url")
            or live_tv_defaults.get("guides")
        )
        live_tv_default_program_icon_urls = _coerce_url_list(
            live_tv_defaults.get("default_program_icon_urls")
            or live_tv_defaults.get("default_program_icon_url")
        )
        live_tv_default_program_icon_url = (
            str(live_tv_default_program_icon_urls[0]).strip()
            if live_tv_default_program_icon_urls
            else ""
        )
        if not live_tv_tuner_urls:
            raise ValueError("live_tv_defaults must define at least one tuner URL")
        if not live_tv_guide_urls:
            raise ValueError("live_tv_defaults must define at least one guide URL")
        if not live_tv_default_program_icon_url:
            raise ValueError(
                "live_tv_defaults.default_program_icon_urls (or default_program_icon_url) is required"
            )
    
        return ControllerProfileCatalog(
            deployment_aliases=deployment_aliases,
            purpose_values=purpose_values,
            route_strategy_aliases=route_strategy_aliases,
            auth_providers=auth_providers,
            auth_disabled_provider=auth_disabled_provider,
            auth_provider_middleware_defaults=auth_provider_middleware_defaults,
            app_keys=app_keys,
            app_aliases=app_aliases,
            install_profiles=install_profiles,
            bool_true_tokens=bool_true_tokens,
            bool_false_tokens=bool_false_tokens,
            chaos_default_enabled=chaos_default_enabled,
            chaos_default_duration_minutes=chaos_default_duration_minutes,
            chaos_default_interval_seconds=chaos_default_interval_seconds,
            chaos_allowed_actions=chaos_allowed_actions,
            chaos_default_actions=chaos_default_actions,
            live_tv_tuner_urls=live_tv_tuner_urls,
            live_tv_guide_urls=live_tv_guide_urls,
            live_tv_default_program_icon_url=live_tv_default_program_icon_url,
        )
    
    
    def load_bootstrap_profile_catalog(self, path: Path | None = None) -> Any:
        resolved_path = _resolve_catalog_path(path)
        return _load_bootstrap_profile_catalog_cached(str(resolved_path))
    
    
    def clear_catalog_cache(self) -> None:
        """Clear the LRU cache for the catalog loader.
    
        Call this in tests that modify the service registry (SERVICES) to
        prevent stale catalog data from leaking across test boundaries.
        """
        _load_bootstrap_profile_catalog_cached.cache_clear()


_instance = CatalogLoaderService()
load_bootstrap_profile_catalog = _instance.load_bootstrap_profile_catalog
clear_catalog_cache = _instance.clear_catalog_cache
_load_bootstrap_profile_catalog_cached = CatalogLoaderService._load_bootstrap_profile_catalog_cached
_resolve_catalog_path = _instance._resolve_catalog_path
