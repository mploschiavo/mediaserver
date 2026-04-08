"""Backward-compatible shim -- canonical home is services.apps.jellyfin.media_server_adapter."""

from media_stack.services.apps.jellyfin.media_server_adapter import (  # noqa: F401
    JellyfinMediaServerAdapter,
)

__all__ = ["JellyfinMediaServerAdapter"]
