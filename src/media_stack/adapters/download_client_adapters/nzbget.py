"""NZBGet bootstrap adapter."""

from __future__ import annotations

from media_stack.adapters.download_client_adapters.usenet import (
    GenericUsenetDownloadClientAdapter,
)


class NzbgetDownloadClientAdapter(GenericUsenetDownloadClientAdapter):
    """NZBGet adapter implemented with generic usenet behavior."""

    pass
