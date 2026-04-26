"""Media-integrity adapters — Servarr + Bazarr port implementations.

ADR-0002 Phase 16-E (cross-cutting media-integrity) — port impls
that satisfy the ``ArrApp`` (Servarr family) and ``BazarrApp``
protocols defined in ``media_stack.domain.media_integrity``.

Per-app adapters absorb each *arr's HTTP quirks (the
``autoUnmonitorPreviouslyDownloaded{Movies,Episodes,Tracks,Books}``
field-name family, Sonarr's series→episode flattening, Lidarr/
Readarr's parent/child shape, etc.) so the application-layer
reconciler + enforcer never have to branch on adapter identity.

The legacy ``services.media_integrity.adapters`` import path
remains as a re-export shim through Phase 16-F.
"""

from media_stack.adapters.media_integrity._servarr_base import (
    HttpClient,
    HttpResponse,
    ServarrHttpError,
    UrllibHttpClient,
    _ServarrBaseAdapter,
)
from media_stack.adapters.media_integrity.bazarr_adapter import BazarrAdapter
from media_stack.adapters.media_integrity.lidarr_adapter import LidarrAdapter
from media_stack.adapters.media_integrity.radarr_adapter import RadarrAdapter
from media_stack.adapters.media_integrity.readarr_adapter import ReadarrAdapter
from media_stack.adapters.media_integrity.sonarr_adapter import SonarrAdapter


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
