"""Factory for media-server adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapter_factory import build_adapter_registry, get_adapter_class
from ..plugin_manifest_loader import build_adapter_hook_defaults, load_plugin_manifests
from .base import MediaServerAdapterBase, MediaServerAdapterContext

AdapterClass = type[MediaServerAdapterBase]


@dataclass(frozen=True)
class MediaServerAdapterFactory:
    adapter_class_specs: dict[str, Any] | None = None
    _adapter_classes: dict[str, AdapterClass] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        specs = self.adapter_class_specs
        if specs is None:
            specs = build_adapter_hook_defaults(load_plugin_manifests()).media_server_adapter_classes
        registry = build_adapter_registry(specs, base_class=MediaServerAdapterBase, role="media_server")
        object.__setattr__(self, "_adapter_classes", registry)

    def create(self, backend: str, context: MediaServerAdapterContext) -> MediaServerAdapterBase:
        cls = get_adapter_class(self._adapter_classes, backend, role="media_server")
        return cls(context=context)
