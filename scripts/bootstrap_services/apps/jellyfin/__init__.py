"""Jellyfin app services."""

from .home_rails_service import JellyfinHomeRailsDependencies, JellyfinHomeRailsService
from .libraries_service import JellyfinLibrariesDependencies, JellyfinLibrariesService
from .livetv_service import JellyfinLiveTvDependencies, JellyfinService
from .livetv_source_service import JellyfinLiveTvSourceService
from .livetv_state_service import JellyfinLiveTvStateService
from .playback_service import JellyfinPlaybackDependencies, JellyfinPlaybackService
from .plugins_service import JellyfinPluginsDependencies, JellyfinPluginsService

__all__ = [
    "JellyfinLiveTvDependencies",
    "JellyfinService",
    "JellyfinLiveTvSourceService",
    "JellyfinLiveTvStateService",
    "JellyfinLibrariesDependencies",
    "JellyfinLibrariesService",
    "JellyfinHomeRailsDependencies",
    "JellyfinHomeRailsService",
    "JellyfinPlaybackDependencies",
    "JellyfinPlaybackService",
    "JellyfinPluginsDependencies",
    "JellyfinPluginsService",
    "JellyfinPrewarmDependencies",
    "JellyfinPrewarmService",
]


def __getattr__(name: str):
    if name in {"JellyfinPrewarmDependencies", "JellyfinPrewarmService"}:
        from .prewarm_service import JellyfinPrewarmDependencies, JellyfinPrewarmService

        return {
            "JellyfinPrewarmDependencies": JellyfinPrewarmDependencies,
            "JellyfinPrewarmService": JellyfinPrewarmService,
        }[name]
    raise AttributeError(name)
