"""Jellyfin playback bootstrap service compatibility module."""

from bootstrap_services.jellyfin_playback_service import (
    JellyfinPlaybackDependencies,
    JellyfinPlaybackService,
)

__all__ = ["JellyfinPlaybackDependencies", "JellyfinPlaybackService"]
