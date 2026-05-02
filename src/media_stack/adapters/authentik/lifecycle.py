"""Authentik ``ServiceLifecycle``.

Authentik does have API tokens, but the controller doesn't mint or
manage them — operators provision tokens out-of-band. So Authentik
is a no-API-key service from the lifecycle's perspective. A future
``probe_has_api_key`` could read a controller-side env
(``AUTHENTIK_TOKEN``) once a token-management flow is designed.
"""

from __future__ import annotations

from media_stack.adapters._lifecycle_base import NoApiKeyLifecycleBase
from media_stack.domain.services import ServiceLifecycle


class AuthentikLifecycle(NoApiKeyLifecycleBase):
    service_id = "authentik"
    _default_health_path = "/-/health/live/"


_check: ServiceLifecycle = AuthentikLifecycle()
del _check


__all__ = ["AuthentikLifecycle"]
