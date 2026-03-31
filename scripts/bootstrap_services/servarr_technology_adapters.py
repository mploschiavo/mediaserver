"""Compatibility exports for legacy Servarr adapter imports.

New code should import from ``bootstrap_services.servarr_technologies`` directly.
"""

from __future__ import annotations

from .servarr_technologies import (
    GenericServarrAdapter,
    LidarrAdapter,
    RadarrAdapter,
    ReadarrAdapter,
    ServarrAdapterBase,
    ServarrAdapterContext,
    ServarrAdapterDependencies,
    ServarrAdapterFactory,
    SonarrAdapter,
)

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
