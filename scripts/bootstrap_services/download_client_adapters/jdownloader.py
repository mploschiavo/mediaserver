"""JDownloader bootstrap adapter."""

from __future__ import annotations

from .usenet import GenericUsenetDownloadClientAdapter


class JdownloaderDownloadClientAdapter(GenericUsenetDownloadClientAdapter):
    """JDownloader adapter implemented with generic usenet behavior."""

    pass
