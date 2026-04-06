"""Plex media-server adapter."""

from __future__ import annotations

from .planned import PlannedMediaServerAdapter


class PlexMediaServerAdapter(PlannedMediaServerAdapter):
    """Plex backend adapter driven by configured phase plans."""

    pass
