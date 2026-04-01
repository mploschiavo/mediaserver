"""Runtime service-class resolution and context registry."""

from __future__ import annotations

import importlib
import inspect

from .plugin_manifest_loader import build_adapter_hook_defaults, load_plugin_manifests

_RUNTIME_CONTEXT_ADAPTER_HOOKS: dict[str, object] = {}


def set_runtime_context_cfg(adapter_hooks_cfg=None):
    global _RUNTIME_CONTEXT_ADAPTER_HOOKS
    _RUNTIME_CONTEXT_ADAPTER_HOOKS = dict(adapter_hooks_cfg or {})


def _manifest_adapter_hooks() -> dict[str, object]:
    defaults = build_adapter_hook_defaults(load_plugin_manifests())
    return defaults.to_dict()


def _active_adapter_hooks() -> dict[str, object]:
    if isinstance(_RUNTIME_CONTEXT_ADAPTER_HOOKS, dict) and _RUNTIME_CONTEXT_ADAPTER_HOOKS:
        return dict(_RUNTIME_CONTEXT_ADAPTER_HOOKS)
    return _manifest_adapter_hooks()


def _load_class_from_spec(spec, *, path_label):
    raw = str(spec or "").strip()
    if ":" not in raw:
        raise RuntimeError(
            f"{path_label}: invalid class spec '{raw}' "
            "(expected 'module.submodule:ClassName')."
        )
    module_name, class_name = raw.rsplit(":", 1)
    module_name = module_name.strip()
    class_name = class_name.strip()
    if not module_name or not class_name:
        raise RuntimeError(
            f"{path_label}: invalid class spec '{raw}' "
            "(expected 'module.submodule:ClassName')."
        )
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name, None)
    if not inspect.isclass(cls):
        raise RuntimeError(f"{path_label}: '{raw}' does not resolve to a class.")
    return cls


def resolve_app_service_class(service_key, default_cls):
    key = str(service_key or "").strip()
    if not key:
        return default_cls

    hooks = _active_adapter_hooks()
    if not isinstance(hooks, dict):
        raise RuntimeError("adapter_hooks must be an object/map.")

    service_map = hooks.get("app_service_classes") or {}
    if not isinstance(service_map, dict):
        raise RuntimeError("adapter_hooks.app_service_classes must be an object/map.")

    spec = service_map.get(key)
    if spec is None or str(spec).strip() == "":
        available = ", ".join(sorted(str(name) for name in service_map.keys())) or "<none>"
        raise RuntimeError(
            "No app service class binding found for "
            f"'{key}' in plugin manifests app_service_classes. "
            "Register it in scripts/bootstrap_defaults/plugins/<technology>/manifest.json. "
            f"Available bindings: {available}."
        )

    return _load_class_from_spec(
        spec,
        path_label=f"plugin_manifests.app_service_classes.{key}",
    )
