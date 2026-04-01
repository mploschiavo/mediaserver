"""Plugin-manifest discovery for technology bindings and adapter hooks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PLUGIN_MANIFESTS_DIR = (
    Path(__file__).resolve().parents[1] / "bootstrap_defaults" / "plugins"
)


@dataclass(frozen=True)
class PluginManifest:
    technology: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    adapter_classes: dict[str, str] = field(default_factory=dict)
    before_common_steps: dict[str, str] = field(default_factory=dict)
    app_service_classes: dict[str, str] = field(default_factory=dict)
    service_technology_map: dict[str, str] = field(default_factory=dict)
    operation_handlers: dict[str, str] = field(default_factory=dict)
    capability_defaults: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None


@dataclass(frozen=True)
class AdapterHookDefaults:
    technology_aliases: dict[str, str] = field(default_factory=dict)
    adapter_classes: dict[str, str] = field(default_factory=dict)
    download_client_adapter_classes: dict[str, str] = field(default_factory=dict)
    media_server_adapter_classes: dict[str, str] = field(default_factory=dict)
    before_common_steps: dict[str, str] = field(default_factory=dict)
    app_service_classes: dict[str, str] = field(default_factory=dict)
    service_technology_map: dict[str, str] = field(default_factory=dict)
    operation_handlers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "technology_aliases": dict(self.technology_aliases),
            "adapter_classes": dict(self.adapter_classes),
            "download_client_adapter_classes": dict(self.download_client_adapter_classes),
            "media_server_adapter_classes": dict(self.media_server_adapter_classes),
            "before_common_steps": dict(self.before_common_steps),
            "app_service_classes": dict(self.app_service_classes),
            "service_technology_map": dict(self.service_technology_map),
            "operation_handlers": dict(self.operation_handlers),
        }


def _iter_manifest_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return ()
    return sorted(path for path in root.rglob("manifest.json") if path.is_file())


def _to_non_empty_str(value: Any) -> str:
    token = str(value or "").strip()
    return token


def _coerce_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, item in value.items():
        k = _to_non_empty_str(key)
        v = _to_non_empty_str(item)
        if k and v:
            out[k] = v
    return out


def _coerce_aliases(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    seen: list[str] = []
    for item in value:
        token = _to_non_empty_str(item).lower()
        if token and token not in seen:
            seen.append(token)
    return tuple(seen)


def _load_manifest(path: Path) -> PluginManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Plugin manifest must be an object: {path}")

    technology = _to_non_empty_str(payload.get("technology"))
    if not technology:
        raise ValueError(f"Plugin manifest missing required 'technology': {path}")

    adapter_classes = _coerce_str_map(payload.get("adapter_classes"))
    return PluginManifest(
        technology=technology.lower(),
        aliases=_coerce_aliases(payload.get("aliases")),
        adapter_classes=adapter_classes,
        before_common_steps=_coerce_str_map(payload.get("before_common_steps")),
        app_service_classes=_coerce_str_map(payload.get("app_service_classes")),
        service_technology_map=_coerce_str_map(payload.get("service_technology_map")),
        operation_handlers=_coerce_str_map(payload.get("operation_handlers")),
        capability_defaults=dict(payload.get("capability_defaults") or {}),
        source_path=path,
    )


def load_plugin_manifests(manifest_root: Path | None = None) -> list[PluginManifest]:
    root = manifest_root or DEFAULT_PLUGIN_MANIFESTS_DIR
    manifests: list[PluginManifest] = []
    for path in _iter_manifest_files(root):
        manifests.append(_load_manifest(path))
    return manifests


def build_adapter_hook_defaults(manifests: list[PluginManifest]) -> AdapterHookDefaults:
    technology_aliases: dict[str, str] = {}
    adapter_classes: dict[str, str] = {}
    download_client_adapter_classes: dict[str, str] = {}
    media_server_adapter_classes: dict[str, str] = {}
    before_common_steps: dict[str, str] = {}
    app_service_classes: dict[str, str] = {}
    service_technology_map: dict[str, str] = {}
    operation_handlers: dict[str, str] = {}

    for manifest in manifests:
        technology = manifest.technology
        for alias in manifest.aliases:
            technology_aliases[alias] = technology

        role_map = manifest.adapter_classes
        servarr_spec = _to_non_empty_str(role_map.get("servarr"))
        if servarr_spec:
            adapter_classes[technology] = servarr_spec

        download_spec = _to_non_empty_str(role_map.get("download_client"))
        if download_spec:
            download_client_adapter_classes[technology] = download_spec

        media_server_spec = _to_non_empty_str(role_map.get("media_server"))
        if media_server_spec:
            media_server_adapter_classes[technology] = media_server_spec

        before_common_steps.update(manifest.before_common_steps)
        app_service_classes.update(manifest.app_service_classes)
        service_technology_map.update(manifest.service_technology_map)
        operation_handlers.update(manifest.operation_handlers)

    return AdapterHookDefaults(
        technology_aliases=technology_aliases,
        adapter_classes=adapter_classes,
        download_client_adapter_classes=download_client_adapter_classes,
        media_server_adapter_classes=media_server_adapter_classes,
        before_common_steps=before_common_steps,
        app_service_classes=app_service_classes,
        service_technology_map=service_technology_map,
        operation_handlers=operation_handlers,
    )


def collect_capability_defaults(manifests: list[PluginManifest]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        defaults = manifest.capability_defaults
        if not isinstance(defaults, dict) or not defaults:
            continue
        merged[manifest.technology] = dict(defaults)
    return merged
