"""Download-client adapter package."""

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
from .qbittorrent import QbittorrentDownloadClientAdapter
from .sabnzbd import SabnzbdDownloadClientAdapter
from .transmission import TransmissionDownloadClientAdapter
from .usenet import GenericUsenetDownloadClientAdapter

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
