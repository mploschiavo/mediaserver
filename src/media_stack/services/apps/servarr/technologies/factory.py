"""Factory for creating per-technology Servarr adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ....adapter_factory import build_adapter_registry, get_adapter_class
from ....plugin_manifest_loader import build_adapter_hook_defaults, load_plugin_manifests
from ....servarr_adapters import AdapterDependencies, HookFn
from .base import ServarrAdapterBase, ServarrAdapterContext, ServarrAdapterDependencies

AdapterClass = type[ServarrAdapterBase]


@dataclass(frozen=True)
class ServarrAdapterFactory:
    deps: ServarrAdapterDependencies
    adapter_deps: AdapterDependencies
    adapter_class_specs: dict[str, Any] | None = None
    _adapter_classes: dict[str, AdapterClass] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        specs = self.adapter_class_specs
        if specs is None:
            specs = build_adapter_hook_defaults(load_plugin_manifests()).adapter_classes
        registry = build_adapter_registry(specs, base_class=ServarrAdapterBase, role="servarr")
        object.__setattr__(self, "_adapter_classes", registry)

    def create(
        self,
        context: ServarrAdapterContext,
        before_common_hook: HookFn,
    ) -> ServarrAdapterBase:
        cls = get_adapter_class(self._adapter_classes, context.app_impl, role="servarr")
        return cls(
            context=context,
            deps=self.deps,
            adapter_deps=self.adapter_deps,
            before_common_hook=before_common_hook,
        )
