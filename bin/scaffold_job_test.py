#!/usr/bin/env python3
"""Deprecation shim — moved to ``media-stack-scaffold-job-test``.

The real implementation now lives at
``src/media_stack/cli/commands/scaffold_job_test.py`` and ships as
the ``media-stack-scaffold-job-test`` console-script after
``pip install``. This shim exists for one release so existing
tooling and runbooks keep working while callers are updated.
Removed in v1.0.192.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    print(
        "[deprecated] python bin/scaffold_job_test.py — use "
        "`media-stack-scaffold-job-test` (after pip install) or "
        "`python -m media_stack.cli.commands.scaffold_job_test`. "
        "This shim is removed in v1.0.192.",
        file=sys.stderr,
    )
    from media_stack.cli.commands.scaffold_job_test import main
    sys.exit(main())
