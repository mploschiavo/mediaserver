"""Shim — moved to ``media_stack.infrastructure.jellyfin.admin_ops``
in ADR-0002 Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.infrastructure.jellyfin.admin_ops import *  # noqa: F401,F403
from media_stack.infrastructure.jellyfin.admin_ops import (  # noqa: F401
    JellyfinAdminOps,
    discover_admin_user_id,
    discover_api_key,
    hard_reset,
    reset_password,
)
