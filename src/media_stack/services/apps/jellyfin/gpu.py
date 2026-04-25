"""Shim — moved to ``media_stack.infrastructure.jellyfin.gpu`` in
ADR-0002 Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.infrastructure.jellyfin.gpu import *  # noqa: F401,F403
from media_stack.infrastructure.jellyfin.gpu import (  # noqa: F401
    JellyfinGpu,
    build_compose_snippet,
    check_jellyfin_gpu,
    enable_gpu_transcoding,
)
