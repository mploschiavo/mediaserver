"""Generic media-server adapter fallback."""

from __future__ import annotations

from media_stack.domain.media_server_adapters.protocols import (
    MediaServerAdapterBase,
)


class GenericMediaServerAdapter(MediaServerAdapterBase):
    """Fallback adapter (no-op)."""

    pass
