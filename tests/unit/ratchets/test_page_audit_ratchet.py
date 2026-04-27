"""Ratchet: every dashboard route covers the operator-question framework.

The design doc (§5) called for a per-page audit asserting four
quality tiers:

  1. **Loading state** — Skeleton or matching placeholder while the
     primary data hook resolves. Without it, a slow query renders
     a flash of empty content that operators read as "broken".
  2. **Error state** — ``role="alert"`` element with the upstream
     error message. Without it, a 5xx upstream produces a silent
     blank card.
  3. **Empty state** — visible caption when the buffer is empty,
     not a hidden component. Per the empty-state-visibility
     feedback in memory.
  4. **At least one chart on data-heavy routes** — already enforced
     separately in ``test_charts_coverage_ratchet.py``.

This ratchet scans every route's component file plus any
transitively-imported feature file (2 hops) for textual evidence
of the first three. The emphasis on textual evidence is
intentional: a full JSX walk would be brittle, but file-level
strings like ``role="alert"``, ``Skeleton``, and the empty-state
caption are easy to detect and hard to fake.

Per-route exemptions live in ``EXEMPT_ROUTES`` with a one-line
rationale. Adding a new exemption requires deliberate code-review.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_SRC = REPO_ROOT / "ui" / "src"
ROUTES_DIR = UI_SRC / "routes"

EXEMPT_ROUTES = frozenset({
    "auth.tsx",            # /auth — login form, not a data surface
    "api-docs.tsx",        # /api-docs — Swagger embed
    "$.tsx",               # 404
    "$placeholder.tsx",    # placeholder for unbuilt routes
    "__root.tsx",          # router root, no rendering of its own
    "me.tsx",              # /me — single-entity self profile
    "index.tsx",           # / redirects to /ops; cards live there
})

# Patterns that indicate each tier is present.
LOADING_PATTERNS = (
    re.compile(r"\bSkeleton\b"),
    re.compile(r"isLoading\s*\?"),
    re.compile(r"isPending\s*\?"),
    re.compile(r"loading\s*\?"),
)
ERROR_PATTERNS = (
    re.compile(r'role=["\']alert["\']'),
    re.compile(r"\bApiErrorTile\b"),
    re.compile(r"text-danger"),
)
EMPTY_PATTERNS = (
    re.compile(r"\bEmptyState\b"),
    re.compile(r"items?\.length\s*===\s*0"),
    re.compile(r"data-testid=[\"'][\w-]*-empty[\"']"),
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _direct_imports(text: str, anchor: Path) -> set[Path]:
    out: set[Path] = set()
    for m in re.finditer(r'from\s+["\']@/(?P<rel>[^"\']+)["\']', text):
        out |= _resolve_candidates(UI_SRC / m.group("rel"))
    for m in re.finditer(
        r'from\s+["\'](?P<rel>\.{1,2}/[^"\']+)["\']', text,
    ):
        out |= _resolve_candidates((anchor.parent / m.group("rel")).resolve())
    return out


def _resolve_candidates(base: Path) -> set[Path]:
    out: set[Path] = set()
    for ext in (".tsx", ".ts"):
        cand = base.parent / (base.name + ext) if base.suffix == "" else base
        if cand.is_file():
            out.add(cand)
            return out
    if base.is_dir():
        for ext in (".tsx", ".ts"):
            barrel = base / f"index{ext}"
            if barrel.is_file():
                out.add(barrel)
                return out
    return out


def _all_route_files(route: Path) -> set[Path]:
    """Return ``{route}`` plus transitively-imported (≤ 2 hops) local
    feature files so we can scan them all for the three patterns."""
    text = _read(route)
    seen: set[Path] = {route}
    frontier = _direct_imports(text, route)
    seen.update(frontier)
    for f in list(frontier):
        for child in _direct_imports(_read(f), f):
            if child not in seen:
                seen.add(child)
    return seen


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def _scan_routes() -> list[Path]:
    if not ROUTES_DIR.is_dir():
        return []
    return [
        p for p in sorted(ROUTES_DIR.iterdir())
        if p.is_file()
        and p.suffix == ".tsx"
        and not p.name.endswith(".test.tsx")
        and p.name not in EXEMPT_ROUTES
    ]


def _audit_route(route: Path) -> dict[str, bool]:
    files = _all_route_files(route)
    blob = "\n".join(_read(f) for f in files)
    return {
        "loading": _matches_any(blob, LOADING_PATTERNS),
        "error": _matches_any(blob, ERROR_PATTERNS),
        "empty": _matches_any(blob, EMPTY_PATTERNS),
    }


def test_every_route_has_loading_error_empty_states() -> None:
    if not UI_SRC.is_dir():
        return  # No UI checked out.

    failures: list[str] = []
    for route in _scan_routes():
        result = _audit_route(route)
        missing = [k for k, v in result.items() if not v]
        if missing:
            failures.append(
                f"{route.name}: missing {', '.join(missing)} state(s)",
            )

    baseline_path = (
        REPO_ROOT / ".ratchets" / "page-audit-baseline.txt"
    )
    if baseline_path.is_file():
        try:
            baseline = int(
                baseline_path.read_text(encoding="utf-8").strip(),
            )
        except ValueError:
            baseline = -1
    else:
        baseline = -1

    if baseline < 0:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            f"{len(failures)}\n", encoding="utf-8",
        )
        return

    if len(failures) > baseline:
        formatted = "\n".join(f"  - {f}" for f in failures)
        raise AssertionError(
            f"Page-audit coverage regressed from {baseline} to "
            f"{len(failures)} routes missing required states. Each "
            f"route must surface a loading state (Skeleton or "
            f"is{{Loading,Pending}} branch), an error state "
            f"(role=\"alert\" or ApiErrorTile or text-danger), AND "
            f"an empty state (EmptyState component or empty "
            f"caption). Missing routes:\n{formatted}\n\n"
            f"Resolution: add the missing state(s) to the route's "
            f"feature components, OR move the route to "
            f"EXEMPT_ROUTES with a comment explaining why."
        )


def test_baseline_does_not_overshoot_current_count() -> None:
    """Tighten the baseline once a route is brought up to spec."""
    baseline_path = (
        REPO_ROOT / ".ratchets" / "page-audit-baseline.txt"
    )
    if not baseline_path.is_file():
        return
    try:
        baseline = int(baseline_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return
    if baseline <= 0:
        return
    failures = []
    for route in _scan_routes():
        result = _audit_route(route)
        if not all(result.values()):
            failures.append(route.name)
    current = len(failures)
    if baseline - current > 1:
        raise AssertionError(
            f"Page-audit baseline ({baseline}) overshoots current "
            f"failing-routes count ({current}) by {baseline - current}. "
            f"Tighten by editing "
            f".ratchets/page-audit-baseline.txt down to {current}.",
        )
