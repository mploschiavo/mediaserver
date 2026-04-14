"""Typed models for cross-app integration sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BoolSectionConfig:
    """Simple enabled/required section model used by integration toggles."""

    enabled: bool
    required: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BoolSectionConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            raw=src,
        )


@dataclass(frozen=True)
class JellyseerrConfig:
    enabled: bool
    required: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyseerrConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            raw=src,
        )


@dataclass(frozen=True)
class MaintainerrIntegrationsConfig:
    enabled: bool
    required: bool
    test_connections: bool
    sync_rules: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        *,
        default_enabled: bool,
        default_required: bool,
    ) -> "MaintainerrIntegrationsConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", default_enabled)),
            required=bool(src.get("required", default_required)),
            test_connections=bool(src.get("test_connections", True)),
            sync_rules=bool(src.get("sync_rules", False)),
            raw=src,
        )


@dataclass(frozen=True)
class MaintainerrConfig:
    enabled: bool
    required: bool
    integrations: MaintainerrIntegrationsConfig
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MaintainerrConfig":
        src = dict(data or {})
        enabled = bool(src.get("enabled", False))
        required = bool(src.get("required", False))
        integrations_raw = src.get("integrations")
        integrations = MaintainerrIntegrationsConfig.from_dict(
            integrations_raw if isinstance(integrations_raw, dict) else {},
            default_enabled=enabled,
            default_required=required,
        )
        return cls(
            enabled=enabled,
            required=required,
            integrations=integrations,
            raw=src,
        )


@dataclass(frozen=True)
class AppAuthConfig:
    enabled: bool
    required: bool
    method: str
    required_mode: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppAuthConfig":
        src = dict(data or {})
        required_raw = src.get("required")
        required_mode = str(required_raw if required_raw is not None else "Enabled").strip()
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=(
                bool(src.get("required", False)) if isinstance(src.get("required"), bool) else False
            ),
            method=str(src.get("method", "Forms")).strip(),
            required_mode=required_mode or "Enabled",
            raw=src,
        )


@dataclass(frozen=True)
class HomepageConfig(BoolSectionConfig):
    hosts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HomepageConfig":
        src = dict(data or {})
        hosts = [str(x).strip() for x in (src.get("hosts") or []) if str(x).strip()]
        return cls(
            enabled=bool(src.get("enabled", True)),
            required=bool(src.get("required", False)),
            hosts=hosts,
            raw=src,
        )


@dataclass(frozen=True)
class JellyfinHomeRailsConfig:
    enabled: bool
    required: bool
    cleanup_collections_when_disabled: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinHomeRailsConfig":
        src = dict(data or {})
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            cleanup_collections_when_disabled=bool(
                src.get("cleanup_collections_when_disabled", False)
            ),
            raw=src,
        )


@dataclass(frozen=True)
class MediaHygieneConfig(BoolSectionConfig):
    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MediaHygieneConfig":
        section = BoolSectionConfig.from_dict(data)
        return cls(enabled=section.enabled, required=section.required, raw=section.raw)


@dataclass(frozen=True)
class BazarrConfig(BoolSectionConfig):
    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BazarrConfig":
        section = BoolSectionConfig.from_dict(data)
        return cls(enabled=section.enabled, required=section.required, raw=section.raw)


@dataclass(frozen=True)
class JellyfinAutoCollectionsConfig(BoolSectionConfig):
    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JellyfinAutoCollectionsConfig":
        section = BoolSectionConfig.from_dict(data)
        return cls(enabled=section.enabled, required=section.required, raw=section.raw)
