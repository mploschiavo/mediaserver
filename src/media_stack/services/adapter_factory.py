"""Generic adapter factory -- replaces per-role factory boilerplate.

Each adapter role (servarr, download_client, media_server) previously had
its own ~80-line factory with identical spec loading and import logic.
This module provides the shared registry; each role's factory.py becomes
a thin wrapper that calls `create` with role-specific constructor args.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any


class AdapterFactory:
    """Factory for loading and registering adapter classes from spec strings."""

    @staticmethod
    def load_adapter_class(spec: str, base_class: type | None = None, role: str = "adapter") -> Any:
        """Import a class or callable from a 'module.path:Name' spec string."""
        raw = str(spec or "").strip()
        if ":" not in raw:
            raise ValueError(f"Invalid {role} spec '{raw}'. Expected 'module.path:Name'.")
        module_name, attr_name = raw.rsplit(":", 1)
        module_name = module_name.strip()
        attr_name = attr_name.strip()
        if not module_name or not attr_name:
            raise ValueError(f"Invalid {role} spec '{raw}'. Expected 'module.path:Name'.")

        module = importlib.import_module(module_name)
        obj = getattr(module, attr_name, None)
        if obj is None:
            raise TypeError(f"{role.capitalize()} spec '{raw}' -- '{attr_name}' not found in module.")
        if base_class is not None and inspect.isclass(obj) and not issubclass(obj, base_class):
            raise TypeError(f"{role.capitalize()} '{raw}' must inherit from {base_class.__name__}.")
        return obj

    @staticmethod
    def build_adapter_registry(
        class_specs: dict[str, Any],
        base_class: type | None = None,
        role: str = "adapter",
    ) -> dict[str, type]:
        """Build a {key: class} registry from spec strings."""
        registry: dict[str, type] = {}
        for key, spec in (class_specs or {}).items():
            normalized = str(key or "").strip().lower()
            spec_str = str(spec or "").strip()
            if not normalized or not spec_str:
                continue
            registry[normalized] = AdapterFactory.load_adapter_class(spec_str, base_class=base_class, role=role)
        return registry

    @staticmethod
    def get_adapter_class(
        registry: dict[str, type],
        key: str,
        role: str = "adapter",
    ) -> type:
        """Look up an adapter class by key, raising a clear error if missing."""
        normalized = str(key or "").strip().lower()
        if not normalized:
            raise ValueError(f"{role.capitalize()} key must not be empty.")
        cls = registry.get(normalized)
        if cls is None:
            available = ", ".join(sorted(registry.keys())) or "<none>"
            raise ValueError(
                f"No {role} adapter registered for '{normalized}'. "
                f"Declare it in contracts/services/<technology>.yaml "
                f"(plugin.adapter_classes). Registered: {available}."
            )
        return cls


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = AdapterFactory()
load_adapter_class = _instance.load_adapter_class
build_adapter_registry = _instance.build_adapter_registry
get_adapter_class = _instance.get_adapter_class
