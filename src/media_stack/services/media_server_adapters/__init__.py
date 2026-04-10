"""Media-server adapter package.

App-specific adapters (e.g. for media servers) are loaded dynamically
from the service contracts' adapter_classes field — no hardcoded imports.
"""

import importlib

from .base import MediaServerAdapterBase, MediaServerAdapterContext
from .emby import EmbyMediaServerAdapter
from .factory import MediaServerAdapterFactory
from .generic import GenericMediaServerAdapter
from .mythtv import MythTvMediaServerAdapter
from .planned import PlannedMediaServerAdapter


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
