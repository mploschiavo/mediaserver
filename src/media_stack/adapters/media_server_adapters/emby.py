"""Emby media-server adapter."""

from __future__ import annotations

from media_stack.application.media_server_adapters.planned import (
    PlannedMediaServerAdapter,
)


class EmbyMediaServerAdapter(PlannedMediaServerAdapter):
    """Emby backend adapter driven by configured phase plans."""

    pass
