"""Prowlarr app services.

``ProwlarrService`` is exposed via a lazy ``__getattr__`` rather
than a top-of-module import. The class lives at
``application.prowlarr.service.ProwlarrService``; reaching it
through the ``.service`` shim here triggers a circular import
(application.prowlarr.service -> infrastructure.prowlarr
.application_ops shim -> services.apps.prowlarr.application_ops ->
this __init__, which would re-enter the still-loading
application.prowlarr.service). Lazy access defers the resolution
until something actually reads the attribute, by which time
application.prowlarr.service has finished initializing.
"""

from .flaresolverr_service import ProwlarrFlareSolverrService
from .indexer_sync_service import ArrIndexerSyncService
from .pipeline_service import ProwlarrIndexerPipelineService
from .precheck_service import ProwlarrPrecheckService

__all__ = [
    "ArrIndexerSyncService",
    "ProwlarrFlareSolverrService",
    "ProwlarrIndexerPipelineService",
    "ProwlarrPrecheckService",
    "ProwlarrService",
]


def __getattr__(name: str):
    if name == "ProwlarrService":
        from media_stack.application.prowlarr.service import ProwlarrService
        return ProwlarrService
    raise AttributeError(
        f"module 'media_stack.services.apps.prowlarr' has no attribute {name!r}",
    )
