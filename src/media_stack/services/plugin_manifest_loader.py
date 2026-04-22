"""Plugin-manifest discovery for technology bindings and adapter hooks."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .enums import RunnerEvent
import logging

DEFAULT_PLUGIN_MANIFESTS_DIR = (
    Path(__file__).resolve().parents[1] / "contracts" / "plugins"
)


@dataclass(frozen=True)
class PluginManifest:
    technology: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    adapter_classes: dict[str, str] = field(default_factory=dict)
    before_common_steps: dict[str, str] = field(default_factory=dict)
    app_service_classes: dict[str, str] = field(default_factory=dict)
    service_technology_map: dict[str, str] = field(default_factory=dict)
    event_handlers: dict[str, dict[str, str]] = field(default_factory=dict)
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
    app_service_classes_by_technology: dict[str, dict[str, str]] = field(default_factory=dict)
    service_technology_map: dict[str, str] = field(default_factory=dict)
    event_handlers: dict[str, dict[str, str]] = field(default_factory=dict)
    operation_handlers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "technology_aliases": dict(self.technology_aliases),
            "adapter_classes": dict(self.adapter_classes),
            "download_client_adapter_classes": dict(self.download_client_adapter_classes),
            "media_server_adapter_classes": dict(self.media_server_adapter_classes),
            "before_common_steps": dict(self.before_common_steps),
            "app_service_classes": dict(self.app_service_classes),
            "app_service_classes_by_technology": {
                str(technology): dict(service_map)
                for technology, service_map in self.app_service_classes_by_technology.items()
                if isinstance(service_map, dict)
            },
            "service_technology_map": dict(self.service_technology_map),
            "event_handlers": {
                str(event): dict(handlers)
                for event, handlers in self.event_handlers.items()
                if isinstance(handlers, dict)
            },
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


def _coerce_event_handler_map(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for event_name, handler_map in value.items():
        event_key_raw = _to_non_empty_str(event_name)
        if not event_key_raw:
            continue
        if not isinstance(handler_map, dict):
            continue
        try:
            event_key = RunnerEvent.from_value(event_key_raw).value
        except ValueError:
            continue
        out[event_key] = _coerce_str_map(handler_map)
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
    event_handlers = _coerce_event_handler_map(payload.get("event_handlers"))
    legacy_operation_handlers = _coerce_str_map(payload.get("operation_handlers"))
    if legacy_operation_handlers:
        event_handlers.setdefault(RunnerEvent.RUN.value, {}).update(legacy_operation_handlers)

    run_operation_handlers = dict(event_handlers.get(RunnerEvent.RUN.value, {}))

    return PluginManifest(
        technology=technology.lower(),
        aliases=_coerce_aliases(payload.get("aliases")),
        adapter_classes=adapter_classes,
        before_common_steps=_coerce_str_map(payload.get("before_common_steps")),
        app_service_classes=_coerce_str_map(payload.get("app_service_classes")),
        service_technology_map=_coerce_str_map(payload.get("service_technology_map")),
        event_handlers=event_handlers,
        operation_handlers=run_operation_handlers,
        capability_defaults=dict(payload.get("capability_defaults") or {}),
        source_path=path,
    )


def _load_from_service_yamls() -> list[PluginManifest]:
    """Load full plugin definitions from per-service YAML files (contracts/services/*.yaml).

    This reads the complete plugin: section including adapter_classes,
    app_service_classes, service_technology_map, event_handlers, and
    before_common_steps — everything that was in legacy manifest.json files.
    """
    import yaml as _yaml

    svc_dirs = [
        Path(__file__).resolve().parents[2] / "contracts" / "services",
        Path("/opt/media-stack/contracts/services"),
        Path("contracts/services"),
    ]
    manifests: list[PluginManifest] = []
    for svc_dir in svc_dirs:
        if not svc_dir.is_dir() or not any(svc_dir.glob("*.yaml")):
            continue
        for yaml_file in sorted(svc_dir.glob("*.yaml")):
            if yaml_file.name == "_template.yaml":
                continue
            try:
                data = _yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                plugin = data.get("plugin")
                if not isinstance(plugin, dict) or not plugin.get("technology"):
                    continue

                adapter_classes = _coerce_str_map(plugin.get("adapter_classes"))
                ac = plugin.get("adapter_class", "")
                if ac and not adapter_classes:
                    # Only use adapter_class as servarr default when no
                    # explicit adapter_classes are declared
                    adapter_classes["servarr"] = str(ac)

                event_handlers = _coerce_event_handler_map(plugin.get("event_handlers"))
                legacy_ops = _coerce_str_map(plugin.get("operation_handlers"))
                if legacy_ops:
                    event_handlers.setdefault(RunnerEvent.RUN.value, {}).update(legacy_ops)

                manifests.append(PluginManifest(
                    technology=str(plugin["technology"]).lower(),
                    aliases=_coerce_aliases(plugin.get("aliases")),
                    adapter_classes=adapter_classes,
                    before_common_steps=_coerce_str_map(plugin.get("before_common_steps")),
                    app_service_classes=_coerce_str_map(plugin.get("app_service_classes")),
                    service_technology_map=_coerce_str_map(plugin.get("service_technology_map")),
                    event_handlers=event_handlers,
                    operation_handlers=dict(event_handlers.get(RunnerEvent.RUN.value, {})),
                    capability_defaults=dict(plugin.get("capabilities") or plugin.get("capability_defaults") or {}),
                    source_path=yaml_file,
                ))
            except Exception as exc:
                log_swallowed(exc)
        break  # Use first directory found
    return manifests


def load_plugin_manifests(manifest_root: Path | None = None) -> list[PluginManifest]:
    # Prefer per-service YAML files (contracts/services/*.yaml)
    yaml_manifests = _load_from_service_yamls()
    if yaml_manifests:
        # Check if YAML manifests have service class bindings (core manifest)
        has_service_classes = any(m.app_service_classes for m in yaml_manifests)
        if has_service_classes:
            return yaml_manifests

    # Fall back to legacy manifest.json files
    root = manifest_root or DEFAULT_PLUGIN_MANIFESTS_DIR
    manifests: list[PluginManifest] = []
    for path in _iter_manifest_files(root):
        manifests.append(_load_manifest(path))

    # Supplement with any YAML-only technologies
    legacy_techs = {m.technology for m in manifests}
    for ym in yaml_manifests:
        if ym.technology not in legacy_techs:
            manifests.append(ym)

    return manifests


def build_adapter_hook_defaults(manifests: list[PluginManifest]) -> AdapterHookDefaults:
    technology_aliases: dict[str, str] = {}
    adapter_classes: dict[str, str] = {}
    download_client_adapter_classes: dict[str, str] = {}
    media_server_adapter_classes: dict[str, str] = {}
    before_common_steps: dict[str, str] = {}
    app_service_classes: dict[str, str] = {}
    app_service_classes_by_technology: dict[str, dict[str, str]] = {}
    service_technology_map: dict[str, str] = {}
    event_handlers: dict[str, dict[str, str]] = {}
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
        manifest_services = {
            str(key): str(value)
            for key, value in (manifest.app_service_classes or {}).items()
            if str(key).strip() and str(value).strip()
        }
        if manifest_services:
            app_service_classes.update(manifest_services)
            app_service_classes_by_technology.setdefault(technology, {}).update(manifest_services)
        service_technology_map.update(manifest.service_technology_map)
        for event_name, handler_map in (manifest.event_handlers or {}).items():
            event_handlers.setdefault(event_name, {}).update(
                {
                    str(handler_name): str(spec)
                    for handler_name, spec in handler_map.items()
                    if str(handler_name).strip() and str(spec).strip()
                }
            )
        operation_handlers.update(manifest.operation_handlers)

    return AdapterHookDefaults(
        technology_aliases=technology_aliases,
        adapter_classes=adapter_classes,
        download_client_adapter_classes=download_client_adapter_classes,
        media_server_adapter_classes=media_server_adapter_classes,
        before_common_steps=before_common_steps,
        app_service_classes=app_service_classes,
        app_service_classes_by_technology=app_service_classes_by_technology,
        service_technology_map=service_technology_map,
        event_handlers=event_handlers,
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
