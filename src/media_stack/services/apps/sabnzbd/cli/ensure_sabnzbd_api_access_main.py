"""Shim — moved to
``media_stack.infrastructure.sabnzbd.cli_ensure_api_access_main`` in
ADR-0002 Phase 16-D batch 3. Phase 16-F removes this shim.

The bin/ wrappers invoke this module as ``python -m`` via
``run-python-cli.sh``. The shim re-exports the public API for direct
imports AND forwards ``__main__`` to the canonical module so the
shell wrapper still has a CLI to run while Phase 16-D ships.
"""

import sys

from media_stack.infrastructure.sabnzbd.cli_ensure_api_access_main import *  # noqa: F401,F403
from media_stack.infrastructure.sabnzbd.cli_ensure_api_access_main import (  # noqa: F401
    ReconcileResult,
    SAB_RECONCILE_SCRIPT,
    SabnzbdApiAccessConfig,
    SabnzbdApiAccessService,
    build_arg_parser,
    main,
)


if __name__ == "__main__":
    sys.exit(main())
