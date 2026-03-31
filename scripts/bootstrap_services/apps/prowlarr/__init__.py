"""Prowlarr app services."""

from .flaresolverr_service import ProwlarrFlareSolverrService
from .pipeline_service import ProwlarrIndexerPipelineService
from .precheck_service import ProwlarrPrecheckService

__all__ = [
    "ProwlarrService",
    "ProwlarrFlareSolverrService",
    "ProwlarrIndexerPipelineService",
    "ProwlarrPrecheckService",
]


def __getattr__(name: str):
    if name == "ProwlarrService":
        from .service import ProwlarrService

        return ProwlarrService
    raise AttributeError(name)
