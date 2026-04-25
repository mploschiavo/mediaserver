"""Shim — moved to ``media_stack.domain.jellyfin.provider_rules`` in
ADR-0002 Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.domain.jellyfin.provider_rules import *  # noqa: F401,F403
from media_stack.domain.jellyfin.provider_rules import (  # noqa: F401
    JellyfinAdapters,
    apply_artwork_profile,
    normalize_provider_name,
    reorder_provider_names,
)
