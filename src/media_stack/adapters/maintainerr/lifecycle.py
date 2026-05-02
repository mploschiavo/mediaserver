"""Maintainerr ``ServiceLifecycle``.

Maintainerr is a downstream consumer of upstream services' API keys
(Jellyfin, Sonarr, Radarr, Jellyseerr, Tautulli) — no key of its own.
Uses the shared ``NoApiKeyLifecycleBase``.
"""

from __future__ import annotations

from media_stack.adapters._lifecycle_base import NoApiKeyLifecycleBase
from media_stack.domain.services import ServiceLifecycle


class MaintainerrLifecycle(NoApiKeyLifecycleBase):
    service_id = "maintainerr"
    _default_health_path = "/app/maintainerr/api/settings"


_check: ServiceLifecycle = MaintainerrLifecycle()
del _check


__all__ = ["MaintainerrLifecycle"]
