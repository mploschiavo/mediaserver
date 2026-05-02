"""Shim — moved to
``media_stack.infrastructure.jellyfin.cli_ensure_controller_main`` in
ADR-0002 Phase 16-D batch 1. Phase 16-F removes this shim.

The bin/ wrappers invoke this module as ``python -m`` via
``run-python-cli.sh``. The shim re-exports the public API for direct
imports AND forwards ``__main__`` to the canonical module so the
shell wrapper still has a CLI to run while Phase 16-D ships.
"""

import sys

from media_stack.infrastructure.jellyfin.cli_ensure_controller_main import *  # noqa: F401,F403
from media_stack.infrastructure.jellyfin.cli_ensure_controller_main import (  # noqa: F401
    EnsureJellyfinControllerMain,
    main,
)


if __name__ == "__main__":
    sys.exit(main())
