"""Shim — moved to
``media_stack.infrastructure.sabnzbd.cli_ensure_api_access_main`` in
ADR-0002 Phase 16-D batch 3. Phase 16-F removes this shim.
"""

from media_stack.infrastructure.sabnzbd.cli_ensure_api_access_main import *  # noqa: F401,F403
from media_stack.infrastructure.sabnzbd.cli_ensure_api_access_main import (  # noqa: F401
    ReconcileResult,
    SAB_RECONCILE_SCRIPT,
    SabnzbdApiAccessConfig,
    SabnzbdApiAccessService,
    build_arg_parser,
    main,
)
