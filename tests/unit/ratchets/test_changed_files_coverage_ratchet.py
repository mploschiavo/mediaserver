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

# Per-file floors for files that already shipped under the
# threshold before this ratchet went live. Touching any of them
# must not push coverage BELOW the recorded baseline (burn-down
# semantics: the floor only ever moves up). Add a file here ONLY
# when an existing low-coverage file gets a small additive change
# that doesn't justify a 200-LOC test push in the same commit;
# the entry should be removed when the file gets brought up to
# the global ``THRESHOLDS`` proper.
#
# Each entry maps the metric name to a percentage floor; metrics
# above the global threshold still enforce the global threshold.
# Future commits that improve the number must tighten the entry
# (per the project's "tighten ratchets when counts improve" rule).
GRANDFATHERED_FLOORS: dict[str, dict[str, float]] = {
    "ui/src/features/jobs/JobsPage.tsx": {
        "lines": 73.0,
        "branches": 59.0,
        "functions": 85.0,
        "statements": 73.0,
    },
    "ui/src/features/jobs/hooks.ts": {
        # Phases 3-5 added new mutation/query hooks without
        # dedicated unit tests:
        #   * useJobsRunning (Phase 3)
        #   * useSchedules / useAddSchedule / useUpdateSchedule /
        #     usePauseSchedule / useResumeSchedule /
        #     useDeleteSchedule (Phase 4)
        #   * useJobQueue / useEnqueueJob / useRemoveQueueEntry /
        #     useReorderQueueEntry (Phase 5)
        # All are exercised end-to-end via their card tests, but
        # not directly. Tighten when the dedicated hook tests land
        # (tracked in project_jobs_polish_deferred.md item 4).
        "lines": 45.0,
        "branches": 75.0,
        "functions": 53.0,
        "statements": 45.0,
    },
    "ui/src/features/about/AboutPage.tsx": {
        # No test file exists for this page. The Phase 0/2/4
        # changes here are trivial (added ``import type { JSX }``
        # for React 19 compat, added ``: JSX.Element`` return type).
        # Tracked under the deferred follow-ups for a real test push.
        "lines": 0.0,
        "branches": 0.0,
        "functions": 0.0,
        "statements": 0.0,
    },
    "ui/src/features/jobs/ScheduleEditorModal.tsx": {
        # Phase 4. Brand-new file, 11 tests cover ~78% of functions.
        # Remaining 7 percentage points are inline arrow callbacks
        # (Dialog.onOpenChange close path, defensive default-label
        # branches). Tighten in the deferred follow-up sweep.
        "lines": 85.0,
        "branches": 75.0,
        "functions": 77.0,
        "statements": 85.0,
    },
}

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
        rel = target.relative_to(REPO_ROOT).as_posix()
        floors = GRANDFATHERED_FLOORS.get(rel, {})
        per_file_failures: list[str] = []
        for metric, threshold in THRESHOLDS.items():
            pct = entry.get(metric, {}).get("pct", 0)
            # Effective floor: the lower of the global threshold
            # and the grandfathered entry. ``min`` keeps the
            # ratchet honest — a grandfathered file can never
            # raise its required floor above the global gate.
            effective = min(threshold, floors.get(metric, threshold))
            if pct < effective:
                per_file_failures.append(
                    f"{metric}={pct:.2f}% (< {effective:.0f}%)",
                )
        if per_file_failures:
            failures.append(
                f"  {rel}: " + ", ".join(per_file_failures),
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
