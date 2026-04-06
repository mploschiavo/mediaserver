"""Runtime service-class resolution and context registry."""

from __future__ import annotations

import importlib
import inspect

from .plugin_manifest_loader import build_adapter_hook_defaults, load_plugin_manifests

_RUNTIME_CONTEXT_ADAPTER_HOOKS: dict[str, object] = {}


def set_runtime_context_cfg(adapter_hooks_cfg=None):
    global _RUNTIME_CONTEXT_ADAPTER_HOOKS
    _RUNTIME_CONTEXT_ADAPTER_HOOKS = dict(adapter_hooks_cfg or {})


def get_runtime_context_cfg() -> dict[str, object]:
    if not isinstance(_RUNTIME_CONTEXT_ADAPTER_HOOKS, dict):
        return {}
    return dict(_RUNTIME_CONTEXT_ADAPTER_HOOKS)


def get_runtime_binding(binding_key: str, default: str = "") -> str:
    key = str(binding_key or "").strip()
    if not key:
        return str(default or "")
    ctx = get_runtime_context_cfg()
    raw_bindings = ctx.get("runtime_bindings") or {}
    if not isinstance(raw_bindings, dict):
        return str(default or "")
    value = raw_bindings.get(key)
    token = str(value or "").strip()
    if token:
        return token
    return str(default or "")


def _manifest_adapter_hooks() -> dict[str, object]:
    defaults = build_adapter_hook_defaults(load_plugin_manifests())
    return defaults.to_dict()


def _active_adapter_hooks() -> dict[str, object]:
    if isinstance(_RUNTIME_CONTEXT_ADAPTER_HOOKS, dict) and _RUNTIME_CONTEXT_ADAPTER_HOOKS:
        return dict(_RUNTIME_CONTEXT_ADAPTER_HOOKS)
    return _manifest_adapter_hooks()


def _canonical_technology_key(technology: str, hooks: dict[str, object]) -> str:
    token = str(technology or "").strip().lower()
    if not token:
        return ""
    aliases = hooks.get("technology_aliases") or {}
    if isinstance(aliases, dict):
        alias_value = aliases.get(token)
        if alias_value is not None and str(alias_value).strip():
            return str(alias_value).strip().lower()
    return token


def _load_class_from_spec(spec, *, path_label):
    raw = str(spec or "").strip()
    if ":" not in raw:
        raise RuntimeError(
            f"{path_label}: invalid class spec '{raw}' " "(expected 'module.submodule:ClassName')."
        )
    module_name, class_name = raw.rsplit(":", 1)
    module_name = module_name.strip()
    class_name = class_name.strip()
    if not module_name or not class_name:
        raise RuntimeError(
            f"{path_label}: invalid class spec '{raw}' " "(expected 'module.submodule:ClassName')."
        )
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name, None)
    if not inspect.isclass(cls):
        raise RuntimeError(f"{path_label}: '{raw}' does not resolve to a class.")
    return cls


def resolve_app_service_class(service_key, default_cls, technology: str = ""):
    key = str(service_key or "").strip()
    if not key:
        return default_cls

    hooks = _active_adapter_hooks()
    if not isinstance(hooks, dict):
        raise RuntimeError("adapter_hooks must be an object/map.")

    service_map = hooks.get("app_service_classes") or {}
    if not isinstance(service_map, dict):
        raise RuntimeError("adapter_hooks.app_service_classes must be an object/map.")

    spec = None
    if str(technology or "").strip():
        by_technology_map = hooks.get("app_service_classes_by_technology") or {}
        if by_technology_map is not None and not isinstance(by_technology_map, dict):
            raise RuntimeError(
                "adapter_hooks.app_service_classes_by_technology must be an object/map."
            )
        if isinstance(by_technology_map, dict):
            canonical_technology = _canonical_technology_key(technology, hooks)
            technology_map = by_technology_map.get(canonical_technology) or {}
            if technology_map is not None and not isinstance(technology_map, dict):
                raise RuntimeError(
                    "adapter_hooks.app_service_classes_by_technology."
                    f"{canonical_technology} must be an object/map."
                )
            if isinstance(technology_map, dict):
                spec = technology_map.get(key)

    if spec is None:
        spec = service_map.get(key)

    if spec is None or str(spec).strip() == "":
        available = ", ".join(sorted(str(name) for name in service_map.keys())) or "<none>"
        tech_fragment = ""
        if str(technology or "").strip():
            tech_fragment = f" for technology '{technology}'"
        raise RuntimeError(
            "No app service class binding found for "
            f"'{key}'{tech_fragment} in plugin manifests app_service_classes. "
            "Register it in src/media_stack/contracts/plugins/<technology>/manifest.json. "
            f"Available bindings: {available}."
        )

    return _load_class_from_spec(
        spec,
        path_label=f"plugin_manifests.app_service_classes.{key}",
    )
