"""Homepage ``ServiceLifecycle`` — ADR-0003 Phase 3c.

Homepage is a static dashboard rendering YAML config — no API key,
no auth surface from the controller's perspective.
"""

from __future__ import annotations

from media_stack.adapters._lifecycle_base import NoApiKeyLifecycleBase
from media_stack.domain.services import ServiceLifecycle


class HomepageLifecycle(NoApiKeyLifecycleBase):
    service_id = "homepage"
    _default_health_path = "/"


_check: ServiceLifecycle = HomepageLifecycle()
del _check


__all__ = ["HomepageLifecycle"]
