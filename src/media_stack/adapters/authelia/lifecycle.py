"""Authelia ``ServiceLifecycle`` — ADR-0003 Phase 3c.

Authelia uses session cookies for end-user auth and has no static
API key for the controller to mint. Falls into the no-API-key shape.
"""

from __future__ import annotations

from media_stack.adapters._lifecycle_base import NoApiKeyLifecycleBase
from media_stack.domain.services import ServiceLifecycle


class AutheliaLifecycle(NoApiKeyLifecycleBase):
    service_id = "authelia"
    _default_health_path = "/api/health"


_check: ServiceLifecycle = AutheliaLifecycle()
del _check


__all__ = ["AutheliaLifecycle"]
