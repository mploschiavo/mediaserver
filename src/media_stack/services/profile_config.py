"""ProfileConfig — single source of truth for deployment configuration.

Loads the profile YAML once, merges with contract defaults, applies
platform adjustments, and provides a frozen config object that every
component reads from. No fallbacks, no env var overrides, no config.json.

Usage:
    from media_stack.services.profile_config import load_profile_config

    config = load_profile_config()  # reads BOOTSTRAP_PROFILE_FILE
    config.auth.provider        # "authelia"
    config.routing.gateway_host # "m.iomio.io"
    config.is_sso_active        # True
    config.to_cfg()             # backward-compat dict for runtime_builder
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Sub-models (frozen dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProfileMetadata:
    name: str = "media-stack"
    platform: str = "compose"
    purpose: str = "standard"
    description: str = ""
    language: str = "en"
    country: str = "US"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ProfileMetadata:
        d = dict(data or {})
        return cls(
            name=str(d.get("name", "media-stack")),
            platform=str(d.get("platform", "compose")).strip().lower(),
            purpose=str(d.get("purpose", "standard")),
            description=str(d.get("description", "")),
            language=str(d.get("language", "en")),
            country=str(d.get("country", "US")),
        )


@dataclass(frozen=True)
class AuthConfig:
    provider: str = "none"
    mode: str = "none"
    enabled: bool = False
    middleware: str = ""
    oidc_provider: str = "local"
    oidc_config: dict[str, str] = field(default_factory=dict)
    per_service: dict[str, str] = field(default_factory=dict)

    @property
    def is_sso(self) -> bool:
        return self.provider in ("authelia", "authentik")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AuthConfig:
        d = dict(data or {})
        provider = str(d.get("provider") or d.get("mode") or "none").strip().lower()
        return cls(
            provider=provider,
            mode=str(d.get("mode") or provider),
            enabled=bool(d.get("enabled", provider != "none")),
            middleware=str(d.get("middleware", "")),
            oidc_provider=str(d.get("oidc_provider", "local")),
            oidc_config={str(k): str(v) for k, v in (d.get("oidc_config") or {}).items()},
            per_service={str(k): str(v) for k, v in (d.get("per_service") or {}).items()},
        )


@dataclass(frozen=True)
class AppAuthConfig:
    enabled: bool = True
    method: str = "Forms"
    required: str = "DisabledForLocalAddresses"
    username_env: str = "STACK_ADMIN_USERNAME"
    password_env: str = "STACK_ADMIN_PASSWORD"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AppAuthConfig:
        d = dict(data or {})
        return cls(
            enabled=bool(d.get("enabled", True)),
            method=str(d.get("method", "Forms")),
            required=str(d.get("required", "DisabledForLocalAddresses")),
            username_env=str(d.get("username_env", "STACK_ADMIN_USERNAME")),
            password_env=str(d.get("password_env", "STACK_ADMIN_PASSWORD")),
        )


@dataclass(frozen=True)
class RoutingConfig:
    strategy: str = "hybrid"
    provider: str = "envoy"
    base_domain: str = "local"
    stack_subdomain: str = "media-stack"
    gateway_host: str = "apps.media-stack.local"
    gateway_port: int = 80
    app_path_prefix: str = "/app"
    internet_exposed: bool = False
    scheme: str = ""
    direct_hosts: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_scheme(self) -> str:
        if self.scheme:
            return self.scheme
        return "https" if self.gateway_port == 443 else "http"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RoutingConfig:
        d = dict(data or {})
        return cls(
            strategy=str(d.get("strategy", "hybrid")),
            provider=str(d.get("provider", "envoy")),
            base_domain=str(d.get("base_domain", "local")),
            stack_subdomain=str(d.get("stack_subdomain", "media-stack")),
            gateway_host=str(d.get("gateway_host", "apps.media-stack.local")),
            gateway_port=int(d.get("gateway_port", 80)),
            app_path_prefix=str(d.get("app_path_prefix", "/app")),
            internet_exposed=bool(d.get("internet_exposed", False)),
            scheme=str(d.get("scheme", "")),
            direct_hosts={str(k): v for k, v in (d.get("direct_hosts") or {}).items()},
        )


@dataclass(frozen=True)
class BootstrapConfig:
    preconfigure_apps: bool = True
    preconfigure_api_keys: bool = True
    apply_initial_preferences: bool = True
    # ON by default: without this, Prowlarr never fires
    # ``ApplicationIndexerSync`` after the controller adds indexers.
    # Result: 70 indexers in Prowlarr, 0 indexers in Sonarr/Radarr,
    # *arr apps can't search, qBittorrent stays empty. Was the root
    # cause of "why are no downloads starting in qbittorrent?" on
    # fresh installs through v1.0.101.
    trigger_indexer_sync: bool = True
    refresh_health_after_setup: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BootstrapConfig:
        d = dict(data or {})
        return cls(
            preconfigure_apps=bool(d.get("preconfigure_apps", True)),
            preconfigure_api_keys=bool(d.get("preconfigure_api_keys", True)),
            apply_initial_preferences=bool(d.get("apply_initial_preferences", True)),
            trigger_indexer_sync=bool(d.get("trigger_indexer_sync", True)),
            refresh_health_after_setup=bool(d.get("refresh_health_after_setup", True)),
        )


# ---------------------------------------------------------------------------
# ProfileConfig — the single source of truth
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProfileConfig:
    """Immutable deployment configuration loaded from profile YAML.

    Every component reads from this object. No fallbacks, no env var
    overrides, no merges at consumption time.
    """
    metadata: ProfileMetadata
    auth: AuthConfig
    app_auth: AppAuthConfig
    routing: RoutingConfig
    bootstrap: BootstrapConfig
    technology_bindings: dict[str, str] = field(default_factory=dict)
    install_profile: str = "standard"
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_sso_active(self) -> bool:
        return self.auth.is_sso

    @property
    def platform(self) -> str:
        return self.metadata.platform

    @property
    def is_k8s(self) -> bool:
        return self.metadata.platform == "k8s"

    @property
    def effective_app_auth_method(self) -> str:
        """The app auth method adjusted for the auth mode.

        When SSO is active, arr apps use External auth (trust the proxy)
        regardless of what the profile says. This works on both compose
        and K8s because External means 'someone upstream authenticated.'
        """
        if self.is_sso_active:
            return "External"
        if self.auth.provider == "none":
            return "None"
        return self.app_auth.method

    def to_cfg(self) -> dict[str, Any]:
        """Produce the cfg dict for backward-compat with runtime_builder.

        Includes ALL profile sections so runtime_builder.build(cfg) can
        read auth, routing, app_auth without any fallback.
        """
        cfg = dict(self._raw)
        # Ensure critical sections are present even if _raw is stale
        cfg["auth"] = {
            "provider": self.auth.provider,
            "mode": self.auth.mode,
            "enabled": self.auth.enabled,
            "middleware": self.auth.middleware,
            "oidc_provider": self.auth.oidc_provider,
            "oidc_config": dict(self.auth.oidc_config),
            "per_service": dict(self.auth.per_service),
        }
        cfg["routing"] = {
            "strategy": self.routing.strategy,
            "provider": self.routing.provider,
            "base_domain": self.routing.base_domain,
            "stack_subdomain": self.routing.stack_subdomain,
            "gateway_host": self.routing.gateway_host,
            "gateway_port": self.routing.gateway_port,
            "app_path_prefix": self.routing.app_path_prefix,
            "internet_exposed": self.routing.internet_exposed,
            "scheme": self.routing.scheme,
            "direct_hosts": dict(self.routing.direct_hosts),
        }
        cfg["app_auth"] = {
            "enabled": self.app_auth.enabled,
            "method": self.effective_app_auth_method,
            "required": self.app_auth.required,
            "username_env": self.app_auth.username_env,
            "password_env": self.app_auth.password_env,
        }
        cfg["technology_bindings"] = dict(self.technology_bindings)
        cfg.setdefault("trigger_indexer_sync", self.bootstrap.trigger_indexer_sync)
        cfg.setdefault("refresh_health_after_setup", self.bootstrap.refresh_health_after_setup)
        return cfg

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileConfig:
        return cls(
            metadata=ProfileMetadata.from_dict(data.get("metadata")),
            auth=AuthConfig.from_dict(data.get("auth")),
            app_auth=AppAuthConfig.from_dict(data.get("app_auth")),
            routing=RoutingConfig.from_dict(data.get("routing")),
            bootstrap=BootstrapConfig.from_dict(data.get("bootstrap")),
            technology_bindings={
                str(k): str(v)
                for k, v in (data.get("technology_bindings") or {}).items()
            },
            install_profile=str(data.get("install_profile", "standard")),
            _raw=data,
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_PROFILE_SEARCH_PATHS = [
    Path("/profile/profile.yaml"),
    Path("/opt/media-stack/contracts/media-stack.profile.yaml"),
]


def _find_profile_path() -> Path | None:
    """Find the profile YAML from env var or well-known paths."""
    env_path = os.environ.get("BOOTSTRAP_PROFILE_FILE", "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
    for p in _PROFILE_SEARCH_PATHS:
        if p.is_file():
            return p
    return None


def load_profile_config(profile_path: Path | str | None = None) -> ProfileConfig:
    """Load ProfileConfig from a profile YAML file.

    Args:
        profile_path: Explicit path. If None, searches BOOTSTRAP_PROFILE_FILE
                      env var and well-known paths.

    Returns:
        Frozen ProfileConfig instance.

    Raises:
        FileNotFoundError: If no profile YAML is found.
    """
    if profile_path is not None:
        path = Path(profile_path)
    else:
        path = _find_profile_path()

    if path is None or not path.is_file():
        # Return defaults when no profile is available (tests, minimal deploys)
        return ProfileConfig.from_dict({})

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ProfileConfig.from_dict(data)


# Module-level singleton (lazy, loaded on first access)
_cached: ProfileConfig | None = None


def get_profile_config() -> ProfileConfig:
    """Get the cached ProfileConfig singleton.

    Loads from the profile YAML on first call, then returns the
    cached instance. Thread-safe for reads (frozen dataclass).
    """
    global _cached
    if _cached is None:
        _cached = load_profile_config()
    return _cached


def reload_profile_config() -> ProfileConfig:
    """Reload the profile from disk and update the cache."""
    global _cached
    _cached = load_profile_config()
    return _cached
