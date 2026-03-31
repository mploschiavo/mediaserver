"""Generic media-server adapter fallback."""

from __future__ import annotations

from .base import MediaServerAdapterBase


class GenericMediaServerAdapter(MediaServerAdapterBase):
    """Fallback adapter (no-op)."""

    pass
