"""Shim — moved to ``media_stack.application.media_server_adapters``,
``media_stack.adapters.media_server_adapters``, and
``media_stack.domain.media_server_adapters`` in ADR-0002 Phase 16-E
(media_server_adapters). Phase 16-F removes this shim.

The legacy ``services.media_server_adapters`` package re-exports the
public surface from the relocated layers and preserves the dynamic
``__getattr__`` that loads app-specific adapter classes
(``JellyfinMediaServerAdapter``, ``PlexMediaServerAdapter``, …) by
service id from the ``services.apps.<id>.media_server_adapter``
modules — that lazy-load path is what the bootstrap tests exercise.

This module cannot use the ``sys.modules[__name__] = _impl`` trick
the leaf shims use because it is a package — replacing the package
object would break the ``services.media_server_adapters.<leaf>``
import paths the leaf shims plus ``contracts/services/*.yaml`` rely
on. Instead we re-export the public names explicitly and let the
leaf shims handle per-module aliasing.
"""

from __future__ import annotations

import importlib

from media_stack.adapters.media_server_adapters.emby import EmbyMediaServerAdapter
from media_stack.adapters.media_server_adapters.generic import GenericMediaServerAdapter
from media_stack.adapters.media_server_adapters.mythtv import MythTvMediaServerAdapter
from media_stack.application.media_server_adapters.factory import (
    MediaServerAdapterFactory,
)
from media_stack.application.media_server_adapters.planned import (
    PlannedMediaServerAdapter,
)
from media_stack.domain.media_server_adapters.protocols import (
    MediaServerAdapterBase,
    MediaServerAdapterContext,
)


def _lazy_adapter(module_path: str, class_name: str):
    """Import an adapter class on first access."""
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


# Lazy-loaded from service contracts via __getattr__ below.
# Do NOT set these to None — it would shadow __getattr__.


def _load_adapter_for_service(category: str, adapter_key: str):
    """Load an adapter class from the service registry by category."""
    from media_stack.api.services.registry import SERVICES
    for svc in SERVICES:
        if svc.category == category:
            mod = importlib.import_module(f"media_stack.services.apps.{svc.id}.media_server_adapter")
            cls_name = f"{svc.id.capitalize()}MediaServerAdapter"
            return getattr(mod, cls_name, None)
    return None


def __getattr__(name: str):
    """Module-level __getattr__ for lazy loading of app-specific adapters."""
    global JellyfinMediaServerAdapter, PlexMediaServerAdapter
    # Load from registry by matching adapter class name to service
    if name.endswith("MediaServerAdapter") and name not in (
        "MediaServerAdapterBase", "MediaServerAdapterFactory",
        "GenericMediaServerAdapter", "PlannedMediaServerAdapter",
        "EmbyMediaServerAdapter", "MythTvMediaServerAdapter",
    ):
        # Derive service ID from class name and try to load from app layer
        svc_id = name.replace("MediaServerAdapter", "").lower()
        try:
            mod = importlib.import_module(f"media_stack.services.apps.{svc_id}.media_server_adapter")
            cls = getattr(mod, name)
            globals()[name] = cls
            return cls
        except (ImportError, AttributeError, ModuleNotFoundError):
            # Return a placeholder for inactive/uninstalled services
            return PlannedMediaServerAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MediaServerAdapterBase",
    "MediaServerAdapterContext",
    "MediaServerAdapterFactory",
    "PlannedMediaServerAdapter",
    "GenericMediaServerAdapter",
    "JellyfinMediaServerAdapter",
    "EmbyMediaServerAdapter",
    "PlexMediaServerAdapter",
    "MythTvMediaServerAdapter",
]
