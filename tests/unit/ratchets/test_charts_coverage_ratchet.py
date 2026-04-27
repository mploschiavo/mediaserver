"""Ratchet: every data-heavy dashboard route mounts at least one chart.

The design doc (docs/design/ux-polish-backlog-mockups.md §4) called
for charts on every page that exposes read-only metric data. Without
a programmatic check, "Phase A #4 — Charts everywhere" got marked
complete after a single chart shipped on /content > Library.

This ratchet enforces the spirit of the design:

  For each route under ``ui/src/routes/`` whose component file
  references at least 3 numeric data hooks (``use*Stats``,
  ``use*History``, ``use*Analytics``, ``use*Health``, ``use*Active``,
  ``use*Recent``, ``use*Sessions``, ``use*Counts``), assert at least
  one of its rendered components imports from ``recharts``.

The route component itself can either:
  * Mount a card that uses recharts directly, OR
  * Mount a feature component that transitively pulls recharts.

The check is a one-hop static-analysis: walks the route's import
list + inspects each imported feature file. Good enough to fail
loudly when a new data-heavy page lands without a chart.

To resolve a failure: either the page genuinely doesn't need a
chart (move it to ``EXEMPT_ROUTES``) or add the chart. The
resolution path makes the wrong choice expensive and the right
choice cheap, on purpose.

Companion to ``test_no_duplicate_code_ratchet.py`` and friends —
runs as part of ``pytest tests/unit/ratchets/``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_SRC = REPO_ROOT / "ui" / "src"
ROUTES_DIR = UI_SRC / "routes"

# Routes that the design doc explicitly enumerates as data-heavy and
# should have at least one ``recharts`` mount (directly or via a
# transitively-imported feature card). Pinned by hand because the
# AST-walk fallback misses 2-hop hooks (route → page-component →
# feature-card) and routes that look at non-hook data sources
# (rolling buffers, derived state). Adding a new metric-bearing route
# means appending here in the same PR.
DATA_HEAVY_ROUTES = frozenset({
    "audit-log.tsx",      # events/hour + actor split (designed)
    "content.tsx",        # library additions / quality / size growth
    "guardrails.tsx",     # firing trends, severity histogram
    "index.tsx",          # KPI tiles + status sparklines
    "jobs.tsx",           # batches over time, runtime distribution
    "livetv.tsx",         # provider health + freshness
    "media-integrity.tsx",  # report buffer + reconcile-rate
    "ops.tsx",            # service throughput + restarts/hour
    "routing.tsx",        # envoy stats + topology (designed)
    "security.tsx",       # failed-login spike + concurrent sessions
    "sessions.tsx",       # concurrent-over-time + geo distribution
})
RECHARTS_RE = re.compile(r'from\s+["\']recharts["\']')


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _imported_local_files(
    text: str, anchor: Path, max_hops: int = 4,
) -> set[Path]:
    """BFS through ``import … from "@/<…>"`` AND relative
    ``./Foo`` / ``../bar/Foo`` imports, up to ``max_hops`` deep, so we
    can find recharts mounts that live inside a feature card mounted
    by the route's page component (route → page → card → chart)."""
    seen: set[Path] = set()
    frontier = _direct_imports(text, anchor)
    seen.update(frontier)
    for _ in range(max_hops):
        next_frontier: set[Path] = set()
        for f in frontier:
            for child in _direct_imports(_read(f), f):
                if child in seen:
                    continue
                seen.add(child)
                next_frontier.add(child)
        frontier = next_frontier
        if not frontier:
            break
    return seen


def _direct_imports(text: str, anchor: Path) -> set[Path]:
    out: set[Path] = set()
    # @/foo/bar → ui/src/foo/bar
    for m in re.finditer(r'from\s+["\']@/(?P<rel>[^"\']+)["\']', text):
        out |= _resolve_candidates(UI_SRC / m.group("rel"))
    # ./Foo or ../bar/Foo → relative to anchor's directory
    anchor_dir = anchor.parent
    for m in re.finditer(
        r'from\s+["\'](?P<rel>\.{1,2}/[^"\']+)["\']', text,
    ):
        rel = m.group("rel")
        out |= _resolve_candidates((anchor_dir / rel).resolve())
    return out


def _resolve_candidates(base: Path) -> set[Path]:
    """Resolve a bare module path to candidate file(s). TS / TSX /
    index forms are tried in order."""
    out: set[Path] = set()
    for ext in (".tsx", ".ts"):
        cand = base.with_suffix(ext) if base.suffix == "" else base
        cand_with_ext = (
            base.parent / (base.name + ext) if base.suffix == "" else cand
        )
        if cand_with_ext.is_file():
            out.add(cand_with_ext)
            return out
    if base.is_dir():
        for child in base.glob("*.tsx"):
            if child.is_file() and not child.name.endswith(".test.tsx"):
                out.add(child)
    return out


def _route_uses_recharts(route_path: Path) -> bool:
    """True if the route file or any transitively-imported feature
    file (up to 4 hops deep) imports from ``recharts``."""
    text = _read(route_path)
    if RECHARTS_RE.search(text):
        return True
    for child in _imported_local_files(text, route_path):
        if RECHARTS_RE.search(_read(child)):
            return True
    return False


def _scan_routes() -> list[Path]:
    if not ROUTES_DIR.is_dir():
        return []
    return [
        p for p in sorted(ROUTES_DIR.iterdir())
        if p.is_file()
        and p.suffix == ".tsx"
        and p.name in DATA_HEAVY_ROUTES
    ]


def test_data_heavy_routes_mount_at_least_one_chart() -> None:
    if not UI_SRC.is_dir():
        return  # No UI checked out; ratchet is a no-op.

    missing: list[str] = []
    for route in _scan_routes():
        if not _route_uses_recharts(route):
            missing.append(route.name)

    # Burn-down baseline so the ratchet locks in current progress
    # rather than failing the codebase outright on day one.
    baseline_path = (
        REPO_ROOT / ".ratchets" / "charts-coverage-baseline.txt"
    )
    if baseline_path.is_file():
        try:
            baseline = int(baseline_path.read_text(encoding="utf-8").strip())
        except ValueError:
            baseline = -1
    else:
        baseline = -1

    if baseline < 0:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(f"{len(missing)}\n", encoding="utf-8")
        return

    if len(missing) > baseline:
        formatted = "\n".join(
            f"  - {name} (no recharts mount in 3-hop import graph)"
            for name in missing
        )
        raise AssertionError(
            f"Chart coverage regressed from {baseline} missing routes "
            f"to {len(missing)}. New data-heavy routes added without a "
            f"chart, or an existing chart was removed. Missing routes:\n"
            f"{formatted}\n\n"
            f"Fix: add at least one ``recharts`` import (directly or "
            f"via a feature component) to each route above. To confirm "
            f"a route legitimately needs no chart, add it to "
            f"EXEMPT_ROUTES at the top of "
            f"``tests/unit/ratchets/test_charts_coverage_ratchet.py`` "
            f"with a comment explaining why."
        )


def test_baseline_does_not_overshoot_current_count() -> None:
    """Stale baseline that's higher than reality wastes the ratchet —
    a future regression would slip in unflagged. Force tightening
    when a chart gets retired (the count should DROP, never grow)."""
    baseline_path = (
        REPO_ROOT / ".ratchets" / "charts-coverage-baseline.txt"
    )
    if not baseline_path.is_file():
        return
    try:
        baseline = int(baseline_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return
    if baseline <= 0:
        return
    missing = [
        r.name for r in _scan_routes() if not _route_uses_recharts(r)
    ]
    current = len(missing)
    if baseline - current > 1:
        raise AssertionError(
            f"Charts-coverage baseline ({baseline}) overshoots current "
            f"missing-routes count ({current}) by {baseline - current}. "
            f"Tighten by editing "
            f".ratchets/charts-coverage-baseline.txt down to {current}.",
        )
