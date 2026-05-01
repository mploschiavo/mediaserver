"""Authentik ``ServiceLifecycle`` — ADR-0003 Phase 3c.

Authentik does have API tokens, but the controller doesn't mint or
manage them today — operators provision tokens out-of-band. So for
this Phase, Authentik is a no-API-key service from the lifecycle's
perspective. Phase 5 may add a ``probe_has_api_key`` that reads a
controller-side env (``AUTHENTIK_TOKEN``) once the token-management
flow is designed.
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
