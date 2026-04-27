"""Per-file coverage gate on changed files.

Goal: every UI source file touched in the current diff must hit
``lines >= 85``, ``functions >= 85``, ``statements >= 85``, and
``branches >= 75`` — the same thresholds the global aggregate at
``ui/vitest.config.ts`` already enforces, but applied per-file so
coverage drops in a freshly-changed file can't hide behind a deep
codebase aggregate.

Wiring:

1. ``pnpm test --coverage`` (run from ``ui/``) writes
   ``ui/coverage/coverage-summary.json`` thanks to the
   ``"json-summary"`` reporter.
2. This ratchet reads that file and the current git diff, then
   asserts the per-file metrics for any touched ``.ts``/``.tsx``
   file that is *included* in the vitest coverage scope
   (excludes routes/, main.tsx, *.test.tsx, etc. — same exclude
   list as vitest.config.ts).

The diff base is configurable via the ``COVERAGE_BASE_REF``
environment variable (typically ``origin/main`` in CI). When unset,
it defaults to ``HEAD~1`` so locally-running developers see the
gate fire on whatever they just committed.

Skip behavior:

* No ``coverage-summary.json`` → ``pytest.skip`` with a message
  pointing at the prereq command. Keeps the ratchet from blocking
  partial local runs while still failing CI when coverage is
  expected to be present.
* No git history (e.g. ``HEAD~1`` doesn't resolve) → skip. CI
  setups that fetch a shallow clone for ratchet-only jobs won't
  spuriously fail.
* No touched files in the included set → no-op pass. Backend-only
  changes don't trigger a coverage check.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_DIR = REPO_ROOT / "ui"
COVERAGE_SUMMARY = UI_DIR / "coverage" / "coverage-summary.json"

# Same shape as the global thresholds at ui/vitest.config.ts. Keep
# in sync — if vitest's defaults move, update both.
THRESHOLDS = {"lines": 85, "branches": 75, "functions": 85, "statements": 85}

# Same exclude list as ui/vitest.config.ts coverage.exclude. Files
# matched here are not in the coverage scope and therefore not in
# the JSON; we filter the diff list against this set so the ratchet
# doesn't try to grade something vitest never measured.
EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    "/test/",
    "src/main.tsx",
    "src/App.tsx",
    "src/routeTree.ts",
    "src/routes/",
    "src/api/types.ts",
    "vite-env.d.ts",
)
EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".test.ts",
    ".test.tsx",
    ".stories.tsx",
    ".d.ts",
)


def _git_diff_files(base: str) -> list[Path]:
    """Files changed between ``base`` and ``HEAD`` plus uncommitted
    edits AND untracked files in the working tree. Returns absolute
    repo-rooted paths.

    Untracked files matter because brand-new files added in the
    current working tree (``?? path`` in ``git status``) are
    "touched" too — without them the gate would silently let a new
    file ship with weak coverage just because ``git diff`` only
    shows modifications.
    """
    try:
        committed = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except subprocess.CalledProcessError:
        return []
    try:
        # ``--name-only`` without revs lists working-tree-vs-index
        # plus index-vs-HEAD when paired with HEAD.
        uncommitted = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except subprocess.CalledProcessError:
        uncommitted = []
    try:
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except subprocess.CalledProcessError:
        untracked = []
    out: list[Path] = []
    seen: set[str] = set()
    for rel in [*committed, *uncommitted, *untracked]:
        rel = rel.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        out.append(REPO_ROOT / rel)
    return out


def _is_covered_ui_source(path: Path) -> bool:
    """True if ``path`` is a UI .ts/.tsx file in vitest's coverage
    scope (under ``ui/src/``, not in the exclude set)."""
    try:
        rel = path.relative_to(UI_DIR).as_posix()
    except ValueError:
        return False
    if not rel.startswith("src/"):
        return False
    if path.suffix not in {".ts", ".tsx"}:
        return False
    if any(rel.endswith(s) for s in EXCLUDE_SUFFIXES):
        return False
    if any(frag in f"/{rel}" for frag in EXCLUDE_FRAGMENTS):
        return False
    return True


def _coverage_key(path: Path) -> str:
    """Vitest writes coverage keys as absolute filesystem paths.
    Resolve to the canonical form the JSON uses."""
    return str(path.resolve())


def test_changed_files_meet_coverage_thresholds() -> None:
    if not COVERAGE_SUMMARY.is_file():
        pytest.skip(
            "ui/coverage/coverage-summary.json missing — run "
            "`pnpm test --coverage` from ui/ first.",
        )

    base = os.environ.get("COVERAGE_BASE_REF", "HEAD~1")
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", base],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        pytest.skip(
            f"COVERAGE_BASE_REF '{base}' does not resolve — "
            "skipping (likely a shallow clone or initial commit).",
        )

    changed = _git_diff_files(base)
    targets = [p for p in changed if _is_covered_ui_source(p)]
    if not targets:
        # Backend-only or test-only diff. Nothing to grade.
        return

    summary_text = COVERAGE_SUMMARY.read_text(encoding="utf-8")
    summary = json.loads(summary_text)

    failures: list[str] = []
    missing: list[str] = []
    for target in targets:
        key = _coverage_key(target)
        entry = summary.get(key)
        if entry is None:
            # Vitest may have skipped this file (e.g. it imports a
            # binary or is excluded by a coverage pragma). Surface
            # rather than silently pass — a touched file with zero
            # coverage data is a regression.
            missing.append(target.relative_to(REPO_ROOT).as_posix())
            continue
        per_file_failures: list[str] = []
        for metric, threshold in THRESHOLDS.items():
            pct = entry.get(metric, {}).get("pct", 0)
            if pct < threshold:
                per_file_failures.append(
                    f"{metric}={pct:.2f}% (< {threshold}%)",
                )
        if per_file_failures:
            failures.append(
                f"  {target.relative_to(REPO_ROOT).as_posix()}: "
                + ", ".join(per_file_failures),
            )

    if missing or failures:
        lines = ["Per-file coverage gate failed for changed files:"]
        if failures:
            lines.append("\nUnder threshold:")
            lines.extend(failures)
        if missing:
            lines.append("\nNo coverage data (file not in vitest scope?):")
            for m in missing:
                lines.append(f"  {m}")
        lines.append(
            "\nFix: add tests for the changed code, OR if the file "
            "should be excluded, update ui/vitest.config.ts "
            "``coverage.exclude`` AND this ratchet's "
            "``EXCLUDE_FRAGMENTS`` / ``EXCLUDE_SUFFIXES``.",
        )
        raise AssertionError("\n".join(lines))
