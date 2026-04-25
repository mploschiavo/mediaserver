"""Shim — moved to ``media_stack.application.jellyfin.runtime_ops``
in ADR-0002 Phase 16-D batch 1. Phase 16-F removes this shim.

The new module declares an ``__all__`` covering every callable
exposed by the legacy module so ``from <shim> import *`` and
``from <shim> import <name>`` keep working for existing callers and
the contracts/services/jellyfin.yaml entry-point handlers.
"""

from media_stack.application.jellyfin.runtime_ops import *  # noqa: F401,F403
from media_stack.application.jellyfin.runtime_ops import (  # noqa: F401
    JellyfinRuntimeOps,
)
