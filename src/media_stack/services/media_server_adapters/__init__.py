"""Media-server adapter package."""

from .base import MediaServerAdapterBase, MediaServerAdapterContext
from .emby import EmbyMediaServerAdapter
from .factory import MediaServerAdapterFactory
from .generic import GenericMediaServerAdapter
from .jellyfin import JellyfinMediaServerAdapter
from .mythtv import MythTvMediaServerAdapter
from .planned import PlannedMediaServerAdapter
from .plex import PlexMediaServerAdapter

__all__ = [
    "MediaServerAdapterBase",
    "MediaServerAdapterContext",
    "MediaServerAdapterFactory",
    "PlannedMediaServerAdapter",
    "GenericMediaServerAdapter",
    "JellyfinMediaServerAdapter",
    "EmbyMediaServerAdapter",
    "PlexMediaServerAdapter",
    "MythTvMediaServerAdapter",
]
