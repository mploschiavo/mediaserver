"""Per-*arr adapters that satisfy the ``ArrApp`` protocol.

Each adapter is a thin veneer over the Servarr HTTP API that maps
the canonical policy keys onto the app's actual field names
(``autoUnmonitorPreviouslyDownloadedMovies`` vs. ``...Episodes``
etc.). All four Servarr apps share a near-identical HTTP shape so
the heavy lifting lives in ``_ServarrBaseAdapter``.

Bazarr is NOT a Servarr and has its own module (``bazarr_adapter.py``,
turn 3 of the media-integrity build).
"""

from media_stack.services.media_integrity.adapters._servarr_base import (
    HttpClient,
    HttpResponse,
    ServarrHttpError,
    UrllibHttpClient,
    _ServarrBaseAdapter,
)
from media_stack.services.media_integrity.adapters.bazarr_adapter import BazarrAdapter
from media_stack.services.media_integrity.adapters.lidarr_adapter import LidarrAdapter
from media_stack.services.media_integrity.adapters.radarr_adapter import RadarrAdapter
from media_stack.services.media_integrity.adapters.readarr_adapter import ReadarrAdapter
from media_stack.services.media_integrity.adapters.sonarr_adapter import SonarrAdapter


__all__ = [
    "BazarrAdapter",
    "HttpClient",
    "HttpResponse",
    "LidarrAdapter",
    "RadarrAdapter",
    "ReadarrAdapter",
    "ServarrHttpError",
    "SonarrAdapter",
    "UrllibHttpClient",
    "_ServarrBaseAdapter",
]
