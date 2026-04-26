"""JDownloader bootstrap adapter."""

from __future__ import annotations

from media_stack.adapters.download_client_adapters.usenet import (
    GenericUsenetDownloadClientAdapter,
)


class JdownloaderDownloadClientAdapter(GenericUsenetDownloadClientAdapter):
    """JDownloader adapter implemented with generic usenet behavior."""

    pass
