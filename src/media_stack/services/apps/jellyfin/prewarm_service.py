"""Shim — moved to
``media_stack.application.jellyfin.prewarm_service`` in ADR-0002
Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.application.jellyfin.prewarm_service import *  # noqa: F401,F403
from media_stack.application.jellyfin.prewarm_service import (  # noqa: F401
    JellyfinPrewarmDependencies,
    JellyfinPrewarmService,
)
