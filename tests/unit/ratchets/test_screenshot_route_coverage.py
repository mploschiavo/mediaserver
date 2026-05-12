"""Ratchet: the Playwright screenshot spec covers every UI route.

Pins the ``controllerRoutes`` list in ``tests/browser/tests/
screenshot-capture.spec.ts`` against the actual route files under
``ui/src/routes/``. Adding a new page to the UI without an entry
in the spec means the docs/screenshots/apps/ set drifts; CI
catches that here so the gap is closed at PR time rather than
discovered when an operator reads the docs.

Excludes ``__root.tsx`` (layout wrapper, not a route),
``$.tsx`` + ``$placeholder.tsx`` (TanStack Router splat / param
catch-alls), and ``index.tsx`` (covered as the ``dashboard``
entry in the spec).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES_DIR = ROOT / "ui" / "src" / "routes"
SPEC_PATH = (
    ROOT / "tests" / "browser" / "tests" / "screenshot-capture.spec.ts"
)


_NON_ROUTE_FILES = {
    "__root.tsx",       # TanStack layout wrapper
    "$.tsx",            # splat catch-all
    "$placeholder.tsx", # placeholder catch-all
}


_INDEX_ROUTE_FILE = "index.tsx"  # captured under the ``dashboard`` name


class ScreenshotRouteCoverage(unittest.TestCase):
    """Every ``ui/src/routes/*.tsx`` route file must appear in the
    Playwright capture spec's ``controllerRoutes`` list."""

    def test_every_ui_route_has_a_screenshot_entry(self) -> None:
        route_files = sorted(
            p.name for p in ROUTES_DIR.glob("*.tsx")
            if not p.name.endswith(".test.tsx")
        )
        expected_paths: set[str] = set()
        for fname in route_files:
            if fname in _NON_ROUTE_FILES:
                continue
            if fname == _INDEX_ROUTE_FILE:
                expected_paths.add("/")
                continue
            stem = fname.removesuffix(".tsx")
            expected_paths.add(f"/{stem}")

        spec_text = SPEC_PATH.read_text(encoding="utf-8")
        spec_paths = set(
            re.findall(
                r"\{\s*name:\s*'[^']+',\s*path:\s*'([^']+)'",
                spec_text,
            )
        )

        missing = sorted(expected_paths - spec_paths)
        unexpected = sorted(spec_paths - expected_paths)

        self.assertEqual(
            missing, [],
            "UI routes exist without a screenshot-capture entry. "
            "Add each missing path to ``controllerRoutes`` in "
            "``tests/browser/tests/screenshot-capture.spec.ts``.\n"
            f"Missing: {missing}",
        )
        self.assertEqual(
            unexpected, [],
            "Screenshot spec references routes that don't exist in "
            "``ui/src/routes/``. Remove stale entries.\n"
            f"Unexpected: {unexpected}",
        )


if __name__ == "__main__":
    unittest.main()
