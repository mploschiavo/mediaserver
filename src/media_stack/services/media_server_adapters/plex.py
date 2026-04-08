"""Backward-compatible shim -- canonical home is services.apps.plex.media_server_adapter."""

from media_stack.services.apps.plex.media_server_adapter import (  # noqa: F401
    PlexMediaServerAdapter,
)

__all__ = ["PlexMediaServerAdapter"]
