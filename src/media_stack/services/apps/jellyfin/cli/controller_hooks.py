"""Shim — moved to
``media_stack.application.jellyfin.controller_hooks`` in ADR-0002
Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.application.jellyfin.controller_hooks import *  # noqa: F401,F403
from media_stack.application.jellyfin.controller_hooks import (  # noqa: F401
    JellyfinControllerHooks,
    activate_media_server_plugins,
)
