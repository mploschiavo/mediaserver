"""Jellyfin media-server adapter."""

from __future__ import annotations

from .planned import PlannedMediaServerAdapter


class JellyfinMediaServerAdapter(PlannedMediaServerAdapter):
    """Jellyfin-specific bootstrap orchestration."""
