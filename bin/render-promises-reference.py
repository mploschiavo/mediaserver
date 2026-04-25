#!/usr/bin/env python3
"""Deprecation shim — moved to ``media-stack-render-promises``.

The real implementation now lives at
``src/media_stack/cli/commands/render_promises_reference.py`` and
ships as the ``media-stack-render-promises`` console-script after
``pip install``. This shim exists for one release so existing
tooling and runbooks keep working while callers are updated.
Removed in v1.0.192.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    print(
        "[deprecated] python3 bin/render-promises-reference.py — use "
        "`media-stack-render-promises` (after pip install) or "
        "`python -m media_stack.cli.commands.render_promises_reference`. "
        "This shim is removed in v1.0.192.",
        file=sys.stderr,
    )
    from media_stack.cli.commands.render_promises_reference import main
    sys.exit(main())
