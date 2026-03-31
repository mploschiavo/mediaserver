"""Factory for media-server adapters."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any

from ..adapter_reflection import discover_adapter_class
from .base import MediaServerAdapterBase, MediaServerAdapterContext
from .generic import GenericMediaServerAdapter

AdapterClass = type[MediaServerAdapterBase]


def _load_adapter_class_from_spec(spec: str) -> AdapterClass:
    raw = str(spec or "").strip()
    if ":" not in raw:
        raise ValueError(
            f"Invalid media-server adapter spec '{raw}'. Expected 'module.submodule:ClassName'."
        )
    module_name, class_name = raw.rsplit(":", 1)
    module_name = module_name.strip()
    class_name = class_name.strip()
    if not module_name or not class_name:
        raise ValueError(
            f"Invalid media-server adapter spec '{raw}'. Expected 'module.submodule:ClassName'."
        )
    module = importlib.import_module(module_name)
    adapter_cls = getattr(module, class_name, None)
    if not inspect.isclass(adapter_cls):
        raise TypeError(f"Media-server adapter spec '{raw}' does not resolve to a class.")
    if not issubclass(adapter_cls, MediaServerAdapterBase):
        raise TypeError(f"Media-server adapter '{raw}' must inherit MediaServerAdapterBase.")
    return adapter_cls


@dataclass(frozen=True)
class MediaServerAdapterFactory:
    adapter_class_specs: dict[str, Any] | None = None
    _adapter_classes: dict[str, AdapterClass] = field(init=False, repr=False)
    _disabled_keys: set[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        mapping: dict[str, AdapterClass] = {}
        disabled: set[str] = set()
        if self.adapter_class_specs is not None and not isinstance(self.adapter_class_specs, dict):
            raise ValueError("adapter_hooks.media_server_adapter_classes must be an object/map.")

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

    def create(self, backend: str, context: MediaServerAdapterContext) -> MediaServerAdapterBase:
        key = str(backend or "").strip().lower()
        adapter_cls = self._adapter_classes.get(key)
        if adapter_cls is None and key and key not in self._disabled_keys:
            discovered = discover_adapter_class(
                module_prefix="bootstrap_services.media_server_adapters",
                key=key,
                base_class=MediaServerAdapterBase,
                class_suffix="MediaServerAdapter",
            )
            if discovered is not None:
                adapter_cls = discovered
        if adapter_cls is None:
            adapter_cls = GenericMediaServerAdapter
        return adapter_cls(context=context)
