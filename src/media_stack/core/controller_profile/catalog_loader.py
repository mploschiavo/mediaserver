"""Loading and caching of the bootstrap profile catalog YAML."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.auth.provider_registry import merge_auth_provider_defaults
import logging

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

import sys as _sys

# Resolve media-stack.catalog.yaml across deploy modes — same
# install-path bug class as v1.0.231 / v1.0.235; see
# test_install_path_resolvers_ratchet.
_DEFAULT_PROFILE_CATALOG_PATH_CANDIDATES = (
    Path(__file__).resolve().parents[4] / "contracts" / "media-stack.catalog.yaml",
    Path("/opt/media-stack/contracts/media-stack.catalog.yaml"),
    Path(_sys.prefix) / "share" / "media-stack" / "contracts" / "media-stack.catalog.yaml",
    Path("/contracts/media-stack.catalog.yaml"),
)


class CatalogLoaderService:
    """Loader + cache wrapper for the bootstrap profile catalog YAML.

    Per ADR-0012, the previously module-level helper
    ``_resolve_profile_catalog`` is folded onto this class as the
    plain instance method ``resolve_profile_catalog`` (no
    ``@staticmethod``). The module-level singleton ``_INSTANCE``
    carries aliases for every public + underscore-prefixed name so
    callers and ``mock.patch`` users keep resolving unchanged.
    """

    def resolve_profile_catalog(self) -> Path:
        for p in _DEFAULT_PROFILE_CATALOG_PATH_CANDIDATES:
            if p.is_file():
                return p
        return _DEFAULT_PROFILE_CATALOG_PATH_CANDIDATES[0]

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

        payload = CatalogLoaderService._read_catalog_payload(path_token)
        CatalogLoaderService._enrich_apps_from_registry(payload)

        auth_providers, auth_disabled_provider, auth_provider_middleware_defaults = (
            CatalogLoaderService._parse_auth_section(payload)
        )
        app_keys, app_aliases = CatalogLoaderService._parse_apps_section(payload)
        install_profiles = CatalogLoaderService._parse_install_profiles(
            payload, app_keys, app_aliases,
        )
        bool_true_tokens, bool_false_tokens = CatalogLoaderService._parse_boolean_tokens(payload)
        chaos = CatalogLoaderService._parse_chaos_defaults(
            payload, bool_true_tokens, bool_false_tokens,
        )
        live_tv = CatalogLoaderService._parse_live_tv_defaults(payload)

        return ControllerProfileCatalog(
            deployment_aliases=_normalize_alias_dict(
                payload.get("deployment_aliases"), field_name="deployment_aliases",
            ),
            purpose_values=_normalize_string_list(
                payload.get("purpose_values"), field_name="purpose_values",
            ),
            route_strategy_aliases=_normalize_alias_dict(
                payload.get("route_strategy_aliases"), field_name="route_strategy_aliases",
            ),
            auth_providers=auth_providers,
            auth_disabled_provider=auth_disabled_provider,
            auth_provider_middleware_defaults=auth_provider_middleware_defaults,
            app_keys=app_keys,
            app_aliases=app_aliases,
            install_profiles=install_profiles,
            bool_true_tokens=bool_true_tokens,
            bool_false_tokens=bool_false_tokens,
            chaos_default_enabled=chaos["enabled"],
            chaos_default_duration_minutes=chaos["duration_minutes"],
            chaos_default_interval_seconds=chaos["interval_seconds"],
            chaos_allowed_actions=chaos["allowed_actions"],
            chaos_default_actions=chaos["default_actions"],
            live_tv_tuner_urls=live_tv["tuner_urls"],
            live_tv_guide_urls=live_tv["guide_urls"],
            live_tv_default_program_icon_url=live_tv["default_program_icon_url"],
        )

    @staticmethod
    def _read_catalog_payload(path_token: str) -> dict:
        """Read and validate the catalog YAML, falling back to the image path.

        Encapsulates the file-existence probe and YAML-shape validation
        so the main loader can treat the payload as a guaranteed dict.
        """
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
        return payload

    @staticmethod
    def _enrich_apps_from_registry(payload: dict) -> None:
        """Merge live registry app IDs into catalog ``apps.keys``.

        Keeps the YAML-bound catalog in sync with runtime-registered
        services without requiring every new service to be re-added to
        the bootstrap YAML manually.
        """
        try:
            from media_stack.core.service_registry.registry import SERVICES
            registry_app_keys = [s.id for s in SERVICES]
            apps_section = payload.setdefault("apps", {})
            existing_keys = set(apps_section.get("keys", []))
            for key in registry_app_keys:
                if key not in existing_keys:
                    apps_section.setdefault("keys", []).append(key)
        except Exception as exc:
            log_swallowed(exc)

    @staticmethod
    def _parse_auth_section(payload: dict) -> tuple[tuple[str, ...], str, dict]:
        """Return (auth_providers, auth_disabled_provider, middleware_defaults).

        Validates cross-references (disabled provider must be in the
        list; middleware-default keys must exist) in one place instead
        of scattering the raise-points across the loader.
        """
        auth_providers = _normalize_string_list(
            payload.get("auth_providers"), field_name="auth_providers",
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
        return auth_providers, auth_disabled_provider, merge_auth_provider_defaults(
            provider_keys=auth_providers,
            catalog_defaults=normalized_auth_defaults,
        )

    @staticmethod
    def _parse_apps_section(payload: dict) -> tuple[tuple[str, ...], dict[str, str]]:
        """Return (app_keys, app_aliases) from the ``apps`` object."""
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
        return app_keys, app_aliases

    @staticmethod
    def _parse_install_profiles(
        payload: dict,
        app_keys: tuple[str, ...],
        app_aliases: dict[str, str],
    ) -> dict[str, tuple[str, ...]]:
        """Return the install-profile map (profile_name → enabled app tuple).

        Supports ``*`` expansion, alias resolution, and deduping — moving
        this out of the loader keeps the tricky profile-shape branching
        isolated.
        """
        install_profiles_payload = payload.get("install_profiles")
        if not isinstance(install_profiles_payload, dict) or not install_profiles_payload:
            raise ValueError("install_profiles must be a non-empty object")
        app_key_set = set(app_keys)
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
        return install_profiles

    @staticmethod
    def _parse_boolean_tokens(payload: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return (true_tokens, false_tokens) from the catalog's token list."""
        boolean_tokens = payload.get("boolean_tokens")
        if not isinstance(boolean_tokens, dict):
            raise ValueError("boolean_tokens must be an object")
        true_tokens_raw = boolean_tokens.get("true")
        if true_tokens_raw is None:
            true_tokens_raw = boolean_tokens.get(True)
        false_tokens_raw = boolean_tokens.get("false")
        if false_tokens_raw is None:
            false_tokens_raw = boolean_tokens.get(False)
        return (
            _normalize_string_list(true_tokens_raw, field_name="boolean_tokens.true"),
            _normalize_string_list(false_tokens_raw, field_name="boolean_tokens.false"),
        )

    @staticmethod
    def _parse_chaos_defaults(
        payload: dict,
        bool_true_tokens: tuple[str, ...],
        bool_false_tokens: tuple[str, ...],
    ) -> dict[str, Any]:
        """Return the chaos-defaults block, bounded and with actions normalized.

        Clipped into its own helper because the min/max validation on
        each numeric field is policy, not orchestration — the loader
        should only care that it got a well-formed block back.
        """
        chaos_defaults_payload = payload.get("chaos_defaults") or {}
        if not isinstance(chaos_defaults_payload, dict):
            raise ValueError("chaos_defaults must be an object when provided")
        allowed = _normalize_string_list_allow_empty(
            chaos_defaults_payload.get("allowed_actions"),
            field_name="chaos_defaults.allowed_actions",
            default=("restart_container", "pause_container", "network_disconnect"),
        )
        return {
            "enabled": _as_bool_with_tokens(
                chaos_defaults_payload.get("enabled"),
                default=False,
                true_tokens=bool_true_tokens,
                false_tokens=bool_false_tokens,
            ),
            "duration_minutes": _to_positive_int(
                chaos_defaults_payload.get("duration_minutes"),
                default=5,
                field_name="chaos_defaults.duration_minutes",
                minimum=1, maximum=120,
            ),
            "interval_seconds": _to_positive_int(
                chaos_defaults_payload.get("interval_seconds"),
                default=60,
                field_name="chaos_defaults.interval_seconds",
                minimum=0, maximum=3600,
            ),
            "allowed_actions": allowed,
            "default_actions": _normalize_chaos_actions(
                chaos_defaults_payload.get("actions"),
                allowed=allowed,
                default=allowed,
            ),
        }

    @staticmethod
    def _parse_live_tv_defaults(payload: dict) -> dict[str, Any]:
        """Return tuner/guide/icon URLs from ``live_tv_defaults`` (all required)."""
        live_tv_defaults = payload.get("live_tv_defaults")
        if not isinstance(live_tv_defaults, dict):
            raise ValueError("live_tv_defaults must be an object")
        tuner_urls = _coerce_url_list(
            live_tv_defaults.get("tuner_urls")
            or live_tv_defaults.get("tuner_url")
            or live_tv_defaults.get("playlists")
        )
        guide_urls = _coerce_url_list(
            live_tv_defaults.get("guide_urls")
            or live_tv_defaults.get("guide_url")
            or live_tv_defaults.get("guides")
        )
        icon_urls = _coerce_url_list(
            live_tv_defaults.get("default_program_icon_urls")
            or live_tv_defaults.get("default_program_icon_url")
        )
        default_program_icon_url = str(icon_urls[0]).strip() if icon_urls else ""
        if not tuner_urls:
            raise ValueError("live_tv_defaults must define at least one tuner URL")
        if not guide_urls:
            raise ValueError("live_tv_defaults must define at least one guide URL")
        if not default_program_icon_url:
            raise ValueError(
                "live_tv_defaults.default_program_icon_urls (or default_program_icon_url) is required"
            )
        return {
            "tuner_urls": tuner_urls,
            "guide_urls": guide_urls,
            "default_program_icon_url": default_program_icon_url,
        }


    def load_bootstrap_profile_catalog(self, path: Path | None = None) -> Any:
        resolved_path = _resolve_catalog_path(path)
        return _load_bootstrap_profile_catalog_cached(str(resolved_path))


    def clear_catalog_cache(self) -> None:
        """Clear the LRU cache for the catalog loader.

        Call this in tests that modify the service registry (SERVICES) to
        prevent stale catalog data from leaking across test boundaries.
        """
        _load_bootstrap_profile_catalog_cached.cache_clear()


# Module-level singleton + aliases (ADR-0012 pattern).
_INSTANCE = CatalogLoaderService()

# Module-level alias for the folded-on ``resolve_profile_catalog``
# helper preserves the historical underscore-prefixed import surface
# (``_resolve_profile_catalog``).
_resolve_profile_catalog = _INSTANCE.resolve_profile_catalog

# Resolved at import time so ``_resolve_catalog_path``'s default branch
# stays a constant lookup.
_DEFAULT_PROFILE_CATALOG_PATH = _resolve_profile_catalog()

load_bootstrap_profile_catalog = _INSTANCE.load_bootstrap_profile_catalog
clear_catalog_cache = _INSTANCE.clear_catalog_cache
_load_bootstrap_profile_catalog_cached = CatalogLoaderService._load_bootstrap_profile_catalog_cached
_resolve_catalog_path = _INSTANCE._resolve_catalog_path
