"""Backward-compatible shim -- canonical home is services.apps.qbittorrent.download_client_adapter."""

from media_stack.services.apps.qbittorrent.download_client_adapter import (  # noqa: F401
    QbittorrentDownloadClientAdapter,
)

__all__ = ["QbittorrentDownloadClientAdapter"]
