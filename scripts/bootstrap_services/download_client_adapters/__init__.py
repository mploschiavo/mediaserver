"""Download-client adapter package."""

from .base import (
    DownloadClientAdapterBase,
    DownloadClientAdapterContext,
    DownloadClientAdapterDependencies,
)
from .factory import DownloadClientAdapterFactory
from .generic import GenericDownloadClientAdapter
from .qbittorrent import QbittorrentDownloadClientAdapter
from .sabnzbd import SabnzbdDownloadClientAdapter
from .transmission import TransmissionDownloadClientAdapter

__all__ = [
    "DownloadClientAdapterBase",
    "DownloadClientAdapterContext",
    "DownloadClientAdapterDependencies",
    "DownloadClientAdapterFactory",
    "GenericDownloadClientAdapter",
    "QbittorrentDownloadClientAdapter",
    "SabnzbdDownloadClientAdapter",
    "TransmissionDownloadClientAdapter",
]
