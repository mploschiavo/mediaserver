"""Ratchet: no new .sh shell scripts.

The repo is being migrated AWAY from bash scripts because they
don't run on Windows or stock macOS without a full POSIX shell.
Cross-platform deploy / build / debug logic should be Python
modules (importable, testable, runnable via ``python3 -m``).

This ratchet pins the existing 60-script set in
``.ratchets/sh-script-baseline.txt``. The CI gate fails if any
.sh file appears in ``git ls-files`` that is NOT on that list.
Removing scripts from the baseline (i.e. converting them to
Python) is encouraged — the ratchet just refuses NEW additions.

To remove an entry from the baseline:
1. Delete the .sh file from the tree (or convert to Python).
2. Remove its line from ``.ratchets/sh-script-baseline.txt``.

To add a new entry (DON'T — write Python instead):
1. Don't.
2. If you have a genuine portability exception (e.g. a release
   tooling script that only ever runs on a Linux CI runner),
   document the rationale in this docstring AND open a PR that
   adds the path to the baseline file with a `# rationale: ...`
   comment on the same line.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_FILE = REPO_ROOT / ".ratchets" / "sh-script-baseline.txt"


def _git_ls_sh() -> set[str]:
    res = subprocess.run(
        ["git", "ls-files", "*.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def _read_baseline() -> set[str]:
    if not BASELINE_FILE.is_file():
        return set()
    out: set[str] = set()
    for raw in BASELINE_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def test_no_new_shell_scripts_added_outside_baseline() -> None:
    tracked = _git_ls_sh()
    baseline = _read_baseline()

    new_scripts = sorted(tracked - baseline)

    assert not new_scripts, (
        "New .sh shell scripts are not allowed in this repo. "
        "Convert these to Python modules under "
        "``src/media_stack/cli/commands/`` (run via ``python3 -m``):\n  - "
        + "\n  - ".join(new_scripts)
        + "\n\nWhy: bash scripts don't run on Windows or stock macOS "
        "without a full POSIX shell. Python is the cross-platform "
        "default. If you have a genuine portability exception, "
        "document it in the ratchet docstring AND add the path to "
        ".ratchets/sh-script-baseline.txt with an inline rationale."
    )


def test_baseline_does_not_drift_above_tracked() -> None:
    """Catch dead entries: baseline lists a path that no longer
    exists in the tree. Forces a baseline cleanup whenever a script
    is converted to Python or deleted, so the ratchet never
    silently grandfathers a gap."""
    tracked = _git_ls_sh()
    baseline = _read_baseline()
    stale = sorted(baseline - tracked)
    assert not stale, (
        "Baseline lists scripts that are no longer in the tree. "
        "Remove these lines from .ratchets/sh-script-baseline.txt:\n  - "
        + "\n  - ".join(stale)
    )
