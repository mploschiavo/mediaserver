"""Shim — moved to
``media_stack.infrastructure.jellyfin.http_preflight`` in ADR-0002
Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.infrastructure.jellyfin.http_preflight import *  # noqa: F401,F403
from media_stack.infrastructure.jellyfin.http_preflight import (  # noqa: F401
    JellyfinHttpPreflight,
    run_preflight,
)
