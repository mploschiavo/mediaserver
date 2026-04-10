"""Download-client adapter package.

App-specific adapters are loaded dynamically — no hardcoded service imports.
"""

import importlib

from .base import (
    DownloadClientAdapterBase,
    DownloadClientAdapterContext,
    DownloadClientAdapterDependencies,
)
from .factory import DownloadClientAdapterFactory
from .generic import GenericDownloadClientAdapter
from .grabit import GrabitDownloadClientAdapter
from .jdownloader import JdownloaderDownloadClientAdapter
from .nzbget import NzbgetDownloadClientAdapter
from .transmission import TransmissionDownloadClientAdapter
from .usenet import GenericUsenetDownloadClientAdapter

# Lazy-loaded from service contracts via __getattr__ below.


def __getattr__(name: str):
    """Lazy-load app-specific download client adapters from the app layer by name convention."""
    global QbittorrentDownloadClientAdapter, SabnzbdDownloadClientAdapter
    if name.endswith("DownloadClientAdapter") and name not in (
        "DownloadClientAdapterBase", "DownloadClientAdapterFactory",
        "GenericDownloadClientAdapter", "GenericUsenetDownloadClientAdapter",
        "TransmissionDownloadClientAdapter", "NzbgetDownloadClientAdapter",
        "JdownloaderDownloadClientAdapter", "GrabitDownloadClientAdapter",
    ):
        # Derive service ID from class name: QbittorrentDownloadClientAdapter → qbittorrent
        svc_id = name.replace("DownloadClientAdapter", "").lower()
        try:
            mod = importlib.import_module(f"media_stack.services.apps.{svc_id}.download_client_adapter")
            cls = getattr(mod, name)
            globals()[name] = cls
            return cls
        except (ImportError, AttributeError):
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DownloadClientAdapterBase",
    "DownloadClientAdapterContext",
    "DownloadClientAdapterDependencies",
    "DownloadClientAdapterFactory",
    "GenericDownloadClientAdapter",
    "GenericUsenetDownloadClientAdapter",
    "QbittorrentDownloadClientAdapter",
    "SabnzbdDownloadClientAdapter",
    "TransmissionDownloadClientAdapter",
    "NzbgetDownloadClientAdapter",
    "JdownloaderDownloadClientAdapter",
    "GrabitDownloadClientAdapter",
]
