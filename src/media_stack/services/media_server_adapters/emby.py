"""Emby media-server adapter."""

from __future__ import annotations

from .planned import PlannedMediaServerAdapter


class EmbyMediaServerAdapter(PlannedMediaServerAdapter):
    """Emby backend adapter driven by configured phase plans."""

    pass
