"""Generic download-client adapter fallback."""

from __future__ import annotations

from media_stack.domain.download_client_adapters.base import DownloadClientAdapterBase


class GenericDownloadClientAdapter(DownloadClientAdapterBase):
    """Fallback adapter with no side effects."""

    pass
