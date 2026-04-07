"""Factory for download-client adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapter_factory import build_adapter_registry, get_adapter_class
from ..plugin_manifest_loader import build_adapter_hook_defaults, load_plugin_manifests
from .base import (
    DownloadClientAdapterBase,
    DownloadClientAdapterContext,
    DownloadClientAdapterDependencies,
)

AdapterClass = type[DownloadClientAdapterBase]


@dataclass(frozen=True)
class DownloadClientAdapterFactory:
    deps: DownloadClientAdapterDependencies
    adapter_class_specs: dict[str, Any] | None = None
    _adapter_classes: dict[str, AdapterClass] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        specs = self.adapter_class_specs
        if specs is None:
            specs = build_adapter_hook_defaults(load_plugin_manifests()).download_client_adapter_classes
        registry = build_adapter_registry(specs, base_class=DownloadClientAdapterBase, role="download_client")
        object.__setattr__(self, "_adapter_classes", registry)

    def create(self, key: str, context: DownloadClientAdapterContext) -> DownloadClientAdapterBase:
        cls = get_adapter_class(self._adapter_classes, key, role="download_client")
        return cls(context=context, deps=self.deps)
