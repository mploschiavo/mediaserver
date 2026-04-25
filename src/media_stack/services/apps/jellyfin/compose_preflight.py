"""Shim — moved to
``media_stack.infrastructure.jellyfin.compose_preflight`` in ADR-0002
Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.infrastructure.jellyfin.compose_preflight import *  # noqa: F401,F403
from media_stack.infrastructure.jellyfin.compose_preflight import (  # noqa: F401
    JellyfinComposePreflight,
    ensure_compose_jellyfin_bootstrap_access,
)
