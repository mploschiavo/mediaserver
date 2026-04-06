"""MythTV media-server adapter."""

from __future__ import annotations

from .planned import PlannedMediaServerAdapter


class MythTvMediaServerAdapter(PlannedMediaServerAdapter):
    """MythTV backend adapter driven by configured phase plans."""

    pass
