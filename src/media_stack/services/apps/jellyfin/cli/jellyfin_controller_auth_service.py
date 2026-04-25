"""Shim — moved to
``media_stack.infrastructure.jellyfin.controller_auth_service`` in
ADR-0002 Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.infrastructure.jellyfin.controller_auth_service import *  # noqa: F401,F403
from media_stack.infrastructure.jellyfin.controller_auth_service import (  # noqa: F401
    JellyfinBootstrapAuthService,
)
