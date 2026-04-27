"""Ratchet: ADR-0002 migration-shim count can only go DOWN.

A "migration shim" in this codebase is a 10-line module that
contains the literal phrase ``Migration shim`` in its docstring
plus a single ``from <canonical> import *`` re-export. They were
introduced as a low-risk dedup mechanism — the original duplicate
file is replaced with a shim pointing at the canonical version,
which lets every existing import keep resolving while we migrate
call-sites to the canonical path.

The risk: shims rot. If a future change adds NEW logic to a shim
(or a brand-new shim is introduced), the canonical/shim distinction
blurs and the duplicate problem is back. This ratchet prevents
that by pinning the count.

To remove a shim cleanly:

  1. Find every importer of the shim's module path (grep src/ tests/).
  2. Update each importer to use the canonical module path.
  3. Delete the shim file.
  4. Lower the baseline in
     ``.ratchets/shim-count-baseline.txt``.

Mass-removal in one shot has been tried — it broke real circular
imports that the shim layer was implicitly papering over. The
incremental approach (one or two per PR, with smoke imports in
between) is the safe path. The baseline only goes down, never up.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_FILE = REPO_ROOT / ".ratchets" / "shim-count-baseline.txt"
SRC = REPO_ROOT / "src" / "media_stack"

_SHIM_IMPORT_RE = re.compile(r"from\s+[\w.]+\s+import\s+\*")


def _count_shims() -> int:
    if not SRC.is_dir():
        return 0
    count = 0
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "Migration shim" in text and _SHIM_IMPORT_RE.search(text):
            count += 1
    return count


def _read_baseline() -> int:
    if not BASELINE_FILE.is_file():
        return -1
    try:
        return int(BASELINE_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return -1


def test_shim_count_does_not_grow_above_baseline() -> None:
    current = _count_shims()
    baseline = _read_baseline()
    if baseline < 0:
        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_FILE.write_text(f"{current}\n", encoding="utf-8")
        return
    assert current <= baseline, (
        f"Migration-shim count grew from {baseline} to {current}. "
        f"Shims are a temporary pattern — adding new ones means the "
        f"deduplication problem is creeping back. Either:\n"
        f"  1. Inline the new code into the canonical module instead of "
        f"     adding a shim, OR\n"
        f"  2. Land the shim in the same PR that retires another one "
        f"     (net-zero count).\n\n"
        f"Baseline lives at .ratchets/shim-count-baseline.txt — every "
        f"legitimate shim retirement should lower it."
    )


def test_baseline_does_not_overshoot_current_count() -> None:
    """If you retired a shim but forgot to lower the baseline, this
    test catches the slack so the next regression isn't masked."""
    current = _count_shims()
    baseline = _read_baseline()
    if baseline < 0:
        return
    assert baseline - current <= 3, (
        f"Shim baseline ({baseline}) overshoots current count "
        f"({current}) by {baseline - current}. Tighten by editing "
        f".ratchets/shim-count-baseline.txt down to {current}."
    )
