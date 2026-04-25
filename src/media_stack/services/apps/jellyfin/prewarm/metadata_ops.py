"""Shim — moved to
``media_stack.domain.jellyfin.prewarm.metadata_ops`` in ADR-0002
Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.domain.jellyfin.prewarm.metadata_ops import *  # noqa: F401,F403
from media_stack.domain.jellyfin.prewarm.metadata_ops import (  # noqa: F401
    JellyfinMetadataOps,
    item_has_artwork,
    item_has_overview,
    run_artwork_health_check,
    run_metadata_backfill,
)
