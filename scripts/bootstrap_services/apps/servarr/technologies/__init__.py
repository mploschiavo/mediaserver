"""Per-technology Servarr adapters and factory."""

from .base import (
    ServarrAdapterBase,
    ServarrAdapterContext,
    ServarrAdapterDependencies,
)
from .factory import ServarrAdapterFactory
from .generic import GenericServarrAdapter
from .lidarr import LidarrAdapter
from .radarr import RadarrAdapter
from .readarr import ReadarrAdapter
from .sonarr import SonarrAdapter

__all__ = [
    "ServarrAdapterBase",
    "ServarrAdapterContext",
    "ServarrAdapterDependencies",
    "ServarrAdapterFactory",
    "GenericServarrAdapter",
    "SonarrAdapter",
    "RadarrAdapter",
    "LidarrAdapter",
    "ReadarrAdapter",
]
