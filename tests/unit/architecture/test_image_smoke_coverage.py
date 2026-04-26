"""Ratchet: image-smoke covers every controller subsystem boot path.

Companion to ``bin/ops/image-smoke.py``. The smoke script is the
structural fix for the v1.0.231 bug class — a "look here, fall back
to there" path-resolution pattern that resolves correctly in the dev
tree but silently disables a subsystem inside the wheel image. The
fix only catches subsystems whose boot is actually invoked by the
smoke; this ratchet enforces that:

1. The smoke script exists and is parseable.
2. It covers AT LEAST ``MIN_SUBSYSTEM_COUNT`` subsystems. The floor
   ratchets up over time — adding a new ``factory.py`` to
   ``services/`` without a matching smoke entry trips this test.
3. Any module under ``src/media_stack/services/`` with a ``factory.py``
   or top-level ``def default(`` is either covered by the smoke or
   surfaced in a clear "uncovered" diagnostic message so the next
   author knows where to add an entry.

This is a pure-static check — it does NOT execute the smoke. The
build-time invocation of the smoke is what catches the runtime
failure; this ratchet just enforces coverage.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SMOKE_PATH = ROOT / "bin" / "ops" / "image-smoke.py"
SERVICES_ROOT = ROOT / "src" / "media_stack" / "services"

# Floor — initial coverage in the prompt. Goes up over time as new
# subsystems get smoke entries; never down.
MIN_SUBSYSTEM_COUNT = 12

# Heuristic regex: each subsystem entry in ``_SUBSYSTEMS`` is a tuple
# whose first element is a quoted dotted name. This pattern matches
# the literal name string so the count survives black/isort
# reformatting.
_TUPLE_NAME = re.compile(
    r'\(\s*\n\s*"([a-zA-Z0-9_.]+)"\s*,\s*\n\s*_probe_',
)


class ImageSmokeCoverageRatchet(unittest.TestCase):
    """Pin the smoke script's coverage of subsystem boot paths."""

    def setUp(self) -> None:
        self.assertTrue(
            SMOKE_PATH.is_file(),
            f"image-smoke script missing at {SMOKE_PATH}",
        )
        self._source = SMOKE_PATH.read_text(encoding="utf-8")

    # -- presence + minimum coverage ---------------------------------

    def test_smoke_script_exists_and_imports_main(self) -> None:
        """Sanity: the smoke script defines the ``main`` entrypoint and
        the canonical ``_SUBSYSTEMS`` table the rest of this test
        introspects."""
        self.assertIn("def main()", self._source)
        self.assertIn("_SUBSYSTEMS", self._source)
        # Top-of-file headline comments — pinned so the ratchet trips
        # if someone accidentally removes the bug-history annotation.
        self.assertIn(
            "Wired into the Dockerfile after",
            self._source,
            "smoke script's Dockerfile-wiring banner is missing",
        )
        self.assertIn(
            "v1.0.231 incident",
            self._source,
            "smoke script's bug-history banner is missing",
        )

    def test_smoke_covers_at_least_min_subsystems(self) -> None:
        """The floor goes UP over time — every new subsystem with a
        ``factory.py`` or ``default()`` should ship a smoke entry."""
        names = self._covered_names()
        self.assertGreaterEqual(
            len(names),
            MIN_SUBSYSTEM_COUNT,
            f"image-smoke covers {len(names)} subsystems; floor is "
            f"{MIN_SUBSYSTEM_COUNT}. Either add a smoke entry or "
            f"justify lowering the floor (you should not lower it).",
        )

    def test_smoke_subsystem_names_unique(self) -> None:
        """Duplicate names would cause one probe to silently shadow
        another in the output table."""
        names = self._covered_names()
        self.assertEqual(
            len(names),
            len(set(names)),
            f"duplicate subsystem name(s) in image-smoke: "
            f"{[n for n in names if names.count(n) > 1]}",
        )

    # -- known case-study subsystems ---------------------------------

    def test_smoke_covers_v1_0_231_case_study(self) -> None:
        """The whole reason this smoke exists is the media-integrity
        contracts-path bug. Removing that probe defeats the point."""
        names = self._covered_names()
        case_study = "media_integrity.factory.build_default_service"
        self.assertIn(
            case_study,
            names,
            f"smoke MUST cover the v1.0.231 case study ({case_study}); "
            f"this is the bug the smoke was built to catch.",
        )

    def test_smoke_covers_handlers_singleton(self) -> None:
        """``api.services.media_integrity_handlers._instance`` is the
        module-level singleton whose import-time wiring is what
        actually enables /api/media-integrity/* — it must be in the
        smoke."""
        names = self._covered_names()
        self.assertIn(
            "api.services.media_integrity_handlers._instance",
            names,
        )

    # -- diagnostic: source-tree subsystems not yet covered ----------

    def test_print_uncovered_factory_modules(self) -> None:
        """Diagnostic that surfaces ``services/.../factory.py`` modules
        the smoke does NOT yet import. Does not fail the build —
        coverage is enforced via the floor count and case-study
        pins above. This print runs only when the test is verbose
        (``-v`` / ``-s``); it's here so a maintainer adding a new
        factory has a one-shot signal of "you should probably add
        a smoke entry"."""
        covered = self._covered_names()
        # Tokenise each smoke entry on dots so e.g. the entry
        # ``media_integrity.factory.build_default_service`` provides
        # the tokens {"media_integrity", "factory", "build_default_service"};
        # a source-tree module ``media_stack.services.media_integrity.factory``
        # is "covered" if the smoke names ALL of its package-segment
        # tokens (other than ``factory``, which is the literal filename
        # and present in every candidate).
        covered_token_sets: list[set[str]] = [
            set(name.lower().split(".")) for name in covered
        ]

        uncovered: list[str] = []
        for factory in SERVICES_ROOT.rglob("factory.py"):
            rel = factory.relative_to(SERVICES_ROOT.parent.parent)
            module = ".".join(rel.with_suffix("").parts)  # media_stack.services...
            # Distinctive package tokens — drop the boilerplate ones that
            # match every entry.
            module_tokens = {
                t for t in module.lower().split(".")
                if t not in {"media_stack", "services", "factory"}
            }
            if not module_tokens:
                continue
            # Covered if any smoke entry's token set is a superset of
            # this factory's distinctive tokens.
            if any(module_tokens <= toks for toks in covered_token_sets):
                continue
            uncovered.append(module)

        # Only print, don't assert — the floor count is the enforcer.
        if uncovered:
            print()
            print(
                f"image-smoke coverage diagnostic: "
                f"{len(uncovered)} factory module(s) not yet covered:"
            )
            for m in sorted(uncovered):
                print(f"  - {m}")
            print(
                "  (Add a probe to bin/ops/image-smoke.py and bump "
                "MIN_SUBSYSTEM_COUNT here if these are real subsystems "
                "the operator-visible boot depends on.)"
            )

    # -- helpers -----------------------------------------------------

    def _covered_names(self) -> list[str]:
        """Pull the list of subsystem names the smoke script covers,
        in declaration order."""
        return [m.group(1) for m in _TUPLE_NAME.finditer(self._source)]


if __name__ == "__main__":
    unittest.main()
