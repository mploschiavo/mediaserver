#!/usr/bin/env python3
"""Deprecation shim — moved to ``media-stack-probe-promises``.

The real implementation now lives at
``src/media_stack/cli/commands/probe_promises.py`` and ships as the
``media-stack-probe-promises`` console-script after ``pip install``.
This shim exists for one release so existing tooling
(``bin/verify-fresh-install.sh``, operator scripts, runbooks) keeps
working while callers are updated. Removed in v1.0.192.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    print(
        "[deprecated] python3 bin/_probe_promises.py — use "
        "`media-stack-probe-promises` (after pip install) or "
        "`python -m media_stack.cli.commands.probe_promises`. "
        "This shim is removed in v1.0.192.",
        file=sys.stderr,
    )
    from media_stack.cli.commands.probe_promises import main
    sys.exit(main())
