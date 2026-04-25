"""Shim — moved to
``media_stack.infrastructure.jellyfin.controller_kube_service`` in
ADR-0002 Phase 16-D batch 1. Phase 16-F removes this shim.
"""

from media_stack.infrastructure.jellyfin.controller_kube_service import *  # noqa: F401,F403
from media_stack.infrastructure.jellyfin.controller_kube_service import (  # noqa: F401
    PortForward,
    choose_kubectl,
    get_secret,
    patch_secret,
    pick_free_local_port,
    run_cmd,
)
