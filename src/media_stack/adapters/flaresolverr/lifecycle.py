"""FlareSolverr ``ServiceLifecycle`` — ADR-0003 Phase 3c.

FlareSolverr is a Cloudflare-bypass proxy. No API key — Prowlarr
talks to it directly via HTTP and the controller doesn't authenticate
to it.
"""

from __future__ import annotations

from media_stack.adapters._lifecycle_base import NoApiKeyLifecycleBase
from media_stack.domain.services import ServiceLifecycle


class FlaresolverrLifecycle(NoApiKeyLifecycleBase):
    service_id = "flaresolverr"
    _default_health_path = "/"


_check: ServiceLifecycle = FlaresolverrLifecycle()
del _check


__all__ = ["FlaresolverrLifecycle"]
