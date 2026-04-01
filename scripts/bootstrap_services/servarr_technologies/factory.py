"""Factory for creating per-technology Servarr adapters."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any

from ..plugin_manifest_loader import build_adapter_hook_defaults, load_plugin_manifests
from ..servarr_adapters import AdapterDependencies, HookFn
from .base import ServarrAdapterBase, ServarrAdapterContext, ServarrAdapterDependencies

AdapterClass = type[ServarrAdapterBase]


def _load_adapter_class_from_spec(spec: str) -> AdapterClass:
    raw = str(spec or "").strip()
    if ":" not in raw:
        raise ValueError(
            f"Invalid adapter class spec '{raw}'. Expected 'module.submodule:ClassName'."
        )
    module_name, class_name = raw.rsplit(":", 1)
    module_name = module_name.strip()
    class_name = class_name.strip()
    if not module_name or not class_name:
        raise ValueError(
            f"Invalid adapter class spec '{raw}'. Expected 'module.submodule:ClassName'."
        )

    module = importlib.import_module(module_name)
    adapter_cls = getattr(module, class_name, None)
    if not inspect.isclass(adapter_cls):
        raise TypeError(f"Adapter class spec '{raw}' does not resolve to a class.")
    if not issubclass(adapter_cls, ServarrAdapterBase):
        raise TypeError(
            f"Adapter class '{raw}' must inherit from ServarrAdapterBase."
        )
    return adapter_cls


@dataclass(frozen=True)
class ServarrAdapterFactory:
    deps: ServarrAdapterDependencies
    adapter_deps: AdapterDependencies
    adapter_class_specs: dict[str, Any] | None = None
    _adapter_classes: dict[str, AdapterClass] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        mapping: dict[str, AdapterClass] = {}
        class_specs = self.adapter_class_specs
        if class_specs is None:
            defaults = build_adapter_hook_defaults(load_plugin_manifests())
            class_specs = defaults.adapter_classes
        if class_specs is not None and not isinstance(class_specs, dict):
            raise ValueError("adapter_hooks.adapter_classes must be an object/map.")

        for impl, spec in (class_specs or {}).items():
            key = str(impl or "").strip().lower()
            if not key:
                continue
            if spec is None or str(spec).strip() == "":
                continue
            mapping[key] = _load_adapter_class_from_spec(str(spec))

        object.__setattr__(self, "_adapter_classes", mapping)

    def create(
        self,
        context: ServarrAdapterContext,
        before_common_hook: HookFn,
    ) -> ServarrAdapterBase:
        impl = str(context.app_impl).strip().lower()
        if not impl:
            raise ValueError("Servarr app implementation key must not be empty.")
        adapter_cls = self._adapter_classes.get(impl)
        if adapter_cls is None:
            available = ", ".join(sorted(self._adapter_classes.keys())) or "<none>"
            raise ValueError(
                f"No Servarr adapter is registered for '{impl}'. "
                "Declare it in scripts/bootstrap_defaults/plugins/<technology>/manifest.json "
                f"(adapter_classes.servarr). Registered keys: {available}."
            )
        return adapter_cls(
            context=context,
            deps=self.deps,
            adapter_deps=self.adapter_deps,
            before_common_hook=before_common_hook,
        )
