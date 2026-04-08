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
from media_stack.services.apps.qbittorrent.download_client_adapter import QbittorrentDownloadClientAdapter
from media_stack.services.apps.sabnzbd.download_client_adapter import SabnzbdDownloadClientAdapter
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
