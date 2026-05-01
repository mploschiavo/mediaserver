#!/usr/bin/env python3
"""Operator CLI for the run-history repair tool.

Logic lives at ``media_stack.application.jobs.run_history_repair``
(canonical, importable). This file is a thin wrapper so operators
can run it as a script without setting PYTHONPATH:

    python3 bin/ops/repair_run_history.py --apply --older-than-minutes 10

When the package is installed (``pip install -e .`` or container
runtime), the same logic is also importable from
``media_stack.application.jobs.run_history_repair.run_repair``,
which is what the controller's auto-heal cycle uses to satisfy the
``jobs:close-stale-runs`` job.

For full usage, see the module docstring or run with ``--help``.
"""

from __future__ import annotations

import sys
from pathlib import Path


# Allow running this script directly without ``pip install``: prepend
# the repo's ``src/`` to sys.path so the package import below resolves.
# Inside the controller container the package is already installed, so
# this insert is a no-op (the canonical location wins).
_REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))


from media_stack.application.jobs.run_history_repair import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
