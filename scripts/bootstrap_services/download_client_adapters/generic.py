"""Generic download-client adapter fallback."""

from __future__ import annotations

from .base import DownloadClientAdapterBase


class GenericDownloadClientAdapter(DownloadClientAdapterBase):
    """Fallback adapter with no side effects."""

    pass
