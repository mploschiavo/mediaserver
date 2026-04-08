"""Jellyfin media-server adapter."""

from __future__ import annotations

from media_stack.services.media_server_adapters.planned import PlannedMediaServerAdapter


class JellyfinMediaServerAdapter(PlannedMediaServerAdapter):
    """Jellyfin-specific bootstrap orchestration."""
