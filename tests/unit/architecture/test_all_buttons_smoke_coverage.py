"""Architecture ratchet — pin the all-buttons smoke's page-coverage
floor.

Companion to ``tests/e2e/test_all_buttons_smoke.py``. The smoke
itself is opt-in (``-m smoke``) and only fires when Playwright + a
reachable cluster are both available, so a green default test run
does NOT prove the page list is intact. This ratchet runs in the
default unit collection and asserts:

1. ``OPERATOR_PAGES`` enumerates at least ``MIN_PAGE_COUNT`` pages.
   The floor bumps every time a new operator-facing route lands —
   keep the floor ahead of the count so we can't quietly regress
   coverage by deleting a page.

2. Every page entry has the keys the smoke expects (``slug``,
   ``path``, ``label``) and a non-empty value for each.

3. No two pages share the same ``slug`` or ``path``. A duplicate
   would mean the smoke walks the same surface twice and fails to
   walk something else.

4. Every path is either ``/`` (the dashboard) or starts with a
   single ``/`` followed by a slug — i.e. we don't accidentally
   register a relative URL or a fully-qualified one.

Why a unit-level ratchet for an e2e test?
-----------------------------------------
The smoke can only fail loudly when there's a cluster on the other
end. A blank ``OPERATOR_PAGES = ()`` would let the smoke pass
trivially (zero failures). This ratchet is the structural
companion: independent of any cluster, it guarantees the smoke
keeps doing useful work.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Floor — bump this when you add a new operator-facing route.
# ---------------------------------------------------------------------------
# Today the smoke enumerates 21 pages. The floor sits at 15 (the
# brief's "AT LEAST 15" minimum) so we're well above water; raise it
# to N-1 the moment a new route lands so the ratchet trips on a
# regression. Paired with the smoke's per-page click-and-assert
# loop, the two together guarantee both *coverage* (this file) and
# *correctness* (the smoke) of the operator-facing UI.
MIN_PAGE_COUNT = 15


def _import_smoke_module():
    """Import the smoke module by file path so this ratchet doesn't
    depend on the ``tests.e2e`` package being importable in every
    pytest collection (e2e files routinely import optional deps).

    We strip out the Playwright import indirection by loading the
    module spec directly — the page list is defined at module top-
    level and doesn't require the package to be installed.
    """
    smoke_path = ROOT / "tests" / "e2e" / "test_all_buttons_smoke.py"
    spec = importlib.util.spec_from_file_location(
        "_all_buttons_smoke_under_test", smoke_path,
    )
    assert spec and spec.loader, (
        f"could not load smoke module from {smoke_path}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AllButtonsSmokeCoverageTests(unittest.TestCase):
    """Static checks over ``OPERATOR_PAGES``. Runs in every default
    pytest collection (no ``-m`` filter)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.smoke = _import_smoke_module()
        cls.pages = tuple(cls.smoke.OPERATOR_PAGES)

    def test_at_least_min_page_count_pages_enumerated(self) -> None:
        self.assertGreaterEqual(
            len(self.pages),
            MIN_PAGE_COUNT,
            f"OPERATOR_PAGES has {len(self.pages)} entries, below "
            f"the floor of {MIN_PAGE_COUNT}. The floor exists to "
            "prevent silent coverage regression — if you removed a "
            "page on purpose, lower the floor in this test in the "
            "same commit so the intent is reviewable.",
        )

    def test_every_page_has_required_keys(self) -> None:
        required = {"slug", "path", "label"}
        for entry in self.pages:
            with self.subTest(entry=entry):
                missing = required - set(entry)
                self.assertFalse(
                    missing,
                    f"page entry {entry!r} missing keys: {missing}",
                )
                for key in required:
                    self.assertTrue(
                        entry[key],
                        f"page entry {entry!r} has empty {key!r}",
                    )

    def test_slugs_are_unique(self) -> None:
        slugs = [p["slug"] for p in self.pages]
        dupes = {s for s in slugs if slugs.count(s) > 1}
        self.assertFalse(
            dupes,
            f"duplicate slug(s) in OPERATOR_PAGES: {sorted(dupes)}. "
            "Each slug must be unique so the smoke output isn't "
            "ambiguous between two same-labelled pages.",
        )

    def test_paths_are_unique(self) -> None:
        paths = [p["path"] for p in self.pages]
        dupes = {s for s in paths if paths.count(s) > 1}
        self.assertFalse(
            dupes,
            f"duplicate path(s) in OPERATOR_PAGES: {sorted(dupes)}. "
            "A duplicate would have the smoke walk the same surface "
            "twice and miss something else.",
        )

    def test_paths_are_well_formed(self) -> None:
        for entry in self.pages:
            with self.subTest(entry=entry):
                path = entry["path"]
                self.assertTrue(
                    path == "/" or (path.startswith("/")
                                    and not path.startswith("//")),
                    f"path {path!r} must be '/' or a single-slash "
                    "rooted path — the smoke concatenates this with "
                    "the base URL and a malformed path produces "
                    "double-slashes or off-host requests.",
                )
                self.assertNotIn(
                    " ", path,
                    f"path {path!r} contains whitespace",
                )

    def test_dashboard_route_present(self) -> None:
        """The dashboard is the canonical entry point — if the smoke
        doesn't walk it, every other coverage claim is moot."""
        slugs = {p["slug"] for p in self.pages}
        self.assertIn(
            "dashboard", slugs,
            "OPERATOR_PAGES is missing the 'dashboard' entry. The "
            "smoke must walk '/' or it isn't testing the most-hit "
            "page in the product.",
        )

    def test_known_high_risk_pages_covered(self) -> None:
        """The three pages whose buttons broke in production. The
        smoke MUST cover all three — they're the reason this ratchet
        exists. Hardcoded to make the regression intent explicit."""
        slugs = {p["slug"] for p in self.pages}
        for required in ("guardrails", "jobs", "media-integrity"):
            self.assertIn(
                required, slugs,
                f"OPERATOR_PAGES is missing {required!r}. Each of "
                "guardrails / jobs / media-integrity shipped a "
                "broken button to production in the v1.0.18x window; "
                "the smoke MUST cover them or it's not protecting "
                "against the bug-class it was created for.",
            )


if __name__ == "__main__":
    unittest.main()
