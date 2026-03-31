"""Factory for creating per-technology Servarr adapters."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any

from ..adapter_reflection import discover_adapter_class
from ..servarr_adapters import AdapterDependencies, HookFn
from .base import ServarrAdapterBase, ServarrAdapterContext, ServarrAdapterDependencies
from .generic import GenericServarrAdapter
from .lidarr import LidarrAdapter
from .radarr import RadarrAdapter
from .readarr import ReadarrAdapter
from .sonarr import SonarrAdapter

AdapterClass = type[ServarrAdapterBase]

_DEFAULT_ADAPTER_CLASSES: dict[str, AdapterClass] = {
    "sonarr": SonarrAdapter,
    "radarr": RadarrAdapter,
    "lidarr": LidarrAdapter,
    "readarr": ReadarrAdapter,
}


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
    _disabled_keys: set[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        mapping = dict(_DEFAULT_ADAPTER_CLASSES)
        disabled: set[str] = set()
        if self.adapter_class_specs is not None and not isinstance(self.adapter_class_specs, dict):
            raise ValueError("adapter_hooks.adapter_classes must be an object/map.")

        for impl, spec in (self.adapter_class_specs or {}).items():
            key = str(impl or "").strip().lower()
            if not key:
                continue
            if spec is None or str(spec).strip() == "":
                mapping.pop(key, None)
                disabled.add(key)
                continue
            mapping[key] = _load_adapter_class_from_spec(str(spec))

        object.__setattr__(self, "_adapter_classes", mapping)
        object.__setattr__(self, "_disabled_keys", disabled)

    def create(
        self,
        context: ServarrAdapterContext,
        before_common_hook: HookFn,
    ) -> ServarrAdapterBase:
        impl = str(context.app_impl).strip().lower()
        adapter_cls = self._adapter_classes.get(impl)
        if adapter_cls is None and impl and impl not in self._disabled_keys:
            discovered = discover_adapter_class(
                module_prefix="bootstrap_services.servarr_technologies",
                key=impl,
                base_class=ServarrAdapterBase,
                class_suffix="Adapter",
            )
            if discovered is not None:
                adapter_cls = discovered
        if adapter_cls is None:
            adapter_cls = GenericServarrAdapter
        return adapter_cls(
            context=context,
            deps=self.deps,
            adapter_deps=self.adapter_deps,
            before_common_hook=before_common_hook,
        )
