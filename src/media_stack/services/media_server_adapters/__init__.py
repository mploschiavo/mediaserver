"""Media-server adapter package."""

from .base import MediaServerAdapterBase, MediaServerAdapterContext
from .emby import EmbyMediaServerAdapter
from .factory import MediaServerAdapterFactory
from .generic import GenericMediaServerAdapter
from media_stack.services.apps.jellyfin.media_server_adapter import JellyfinMediaServerAdapter
from .mythtv import MythTvMediaServerAdapter
from .planned import PlannedMediaServerAdapter
from media_stack.services.apps.plex.media_server_adapter import PlexMediaServerAdapter

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
