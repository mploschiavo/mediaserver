"""Shim — moved to ``media_stack.adapters.media_integrity`` in
ADR-0002 Phase 16-E (cross-cutting media-integrity). Phase 16-F
removes this shim.

The legacy ``services.media_integrity.adapters`` sub-package re-
exports the public adapter surface from the new location and side-
effect-imports each relocated leaf shim so imports of the form
``services.media_integrity.adapters.{radarr_adapter,sonarr_adapter,...}``
keep resolving against the impl modules.

This package's ``__init__.py`` cannot use the
``sys.modules[__name__] = _impl`` trick the leaf shims use without
breaking the per-leaf import paths. Mirroring the guardrails-batch
precedent, we re-export the public names explicitly and side-effect-
import each leaf shim to register them in ``sys.modules``.
"""

from __future__ import annotations

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

# Side-effect imports — the leaf shims call ``sys.modules[__name__] = _impl``
# at import time, so this loop registers the legacy paths against the
# new impl modules.
from . import (  # noqa: F401,E402  side-effect: alias leaf modules
    _servarr_base,
    bazarr_adapter,
    lidarr_adapter,
    radarr_adapter,
    readarr_adapter,
    sonarr_adapter,
)


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
