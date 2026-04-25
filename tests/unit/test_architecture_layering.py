"""Architecture layering ratchet.

Today's tree:

    src/media_stack/
    ├── api/         HTTP handlers + per-domain service modules
    ├── core/        platform primitives (auth, platforms, edge,
    │                  observability, notifications, events)
    ├── services/    domain logic + per-tech app adapters
    ├── adapters/    abstract adapter framework
    └── cli/         entry-point command modules + shared workflow utils

Rule (currently enforced): ``core/`` is the platform/infrastructure
layer. ``services/`` is the domain layer. Domain depends on platform;
platform must NEVER depend on domain — that's the cycle the layering
rule catches. Without this test, the rule was convention only — the
import graph could grow upward without anyone noticing.

Rules NOT YET enforced (deferred to ADR-0002):

* ``cli/`` is mixed today: ``*_main.py`` entry-points coexist with
  shared utility modules (``cli/commands/job_framework``,
  ``cli/workflows/cli_common``, ``cli/commands/{action,controller}_handlers``)
  that other layers DO import. The bigger refactor under ADR-0002
  Phase 16 splits these into proper homes (``services/jobs/``,
  ``infrastructure/``, etc.) — at which point the cli/ no-import
  rule becomes enforceable. Until then, attempting to ratchet
  cli/ would land 40+ allowlist entries that just track the
  pre-existing tech-debt.

The ratchet shrinks; it must never grow. Each removed violation =
a one-line entry deleted from KNOWN_VIOLATIONS as the matching
refactor lands.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "media_stack"


# Known core/ → services/ violations as of v1.0.190. These are
# imports of legitimately-shared types/helpers that belong in core/
# but live in services/. Real fix: move them down into core/, or
# invert via a Protocol in interfaces/ (post-ADR-0002 Phase 16-A).
KNOWN_VIOLATIONS: dict[str, set[str]] = {
    "core/platforms/compose/controller_service.py": {
        "media_stack.services.top_level_config_model",
    },
    "core/platforms/compose/edge/providers/envoy/dynamic_config.py": {
        "media_stack.services.apps.stack.routing_defaults",
    },
    "core/auth/configure_auth_job.py": {
        "media_stack.services.runtime_platform",
    },
}


def _imports_from(path: Path, prefix: str) -> list[tuple[int, str]]:
    """Return ``(line_no, import_line)`` for every import of
    ``media_stack.<prefix>.*`` in ``path``. Catches both
    ``import media_stack.<prefix>...`` and
    ``from media_stack.<prefix>...``."""
    pattern = re.compile(
        rf"^\s*(?:from\s+media_stack\.{re.escape(prefix)}|"
        rf"import\s+media_stack\.{re.escape(prefix)})",
    )
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return hits
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            hits.append((i, line.strip()))
    return hits


def _files_under(layer: str) -> list[Path]:
    layer_root = ROOT / layer
    if not layer_root.is_dir():
        return []
    return [p for p in layer_root.rglob("*.py") if "__pycache__" not in p.parts]


def _is_known_violation(path: Path, line: str) -> bool:
    """True if this exact (file, import) edge is on the allowlist."""
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        return False
    bucket = KNOWN_VIOLATIONS.get(rel)
    if not bucket:
        return False
    return any(target in line for target in bucket)


class CoreDoesNotDependOnServicesTest(unittest.TestCase):
    """``core/`` must not import from ``services/``.

    Domain (services/) → Platform (core/) is the only valid edge.
    The reverse direction creates an import cycle waiting to fire.
    """

    def test_no_core_imports_from_services(self) -> None:
        offenders: list[str] = []
        for path in _files_under("core"):
            for line_no, line in _imports_from(path, "services"):
                if _is_known_violation(path, line):
                    continue
                rel = path.relative_to(ROOT.parent.parent)
                offenders.append(f"  {rel}:{line_no}: {line}")
        self.assertEqual(
            offenders, [],
            "core/ files must not import from services/. "
            "Domain depends on platform; platform must not depend on "
            "domain. Move the shared abstraction down into core/, or "
            "invert the dependency via a Protocol in interfaces/ "
            "(post-ADR-0002).\n" + "\n".join(offenders),
        )


class KnownViolationsRatchetTest(unittest.TestCase):
    """Lock the count of allowed violations. The list shrinks when
    refactors land; it must NEVER grow. New cross-layer imports
    fail at the source — bumping this allowlist is rejected unless
    the new entry has a same-PR refactor plan."""

    EXPECTED_TOTAL = 3

    def test_count_does_not_grow(self) -> None:
        actual = sum(len(targets) for targets in KNOWN_VIOLATIONS.values())
        self.assertLessEqual(
            actual, self.EXPECTED_TOTAL,
            f"KNOWN_VIOLATIONS grew from {self.EXPECTED_TOTAL} to "
            f"{actual}. New violations require a same-PR refactor "
            f"plan, not just an allowlist bump.",
        )

    def test_entries_actually_exist(self) -> None:
        """Stale entries hide drift — once a refactor removes an
        import, the allowlist entry must come out too."""
        stale: list[str] = []
        for rel_path, targets in KNOWN_VIOLATIONS.items():
            full = ROOT / rel_path
            if not full.is_file():
                stale.append(f"  {rel_path} (file not found)")
                continue
            text = full.read_text(encoding="utf-8")
            for target in targets:
                if target not in text:
                    stale.append(f"  {rel_path}: {target} (not found)")
        self.assertEqual(
            stale, [],
            "KNOWN_VIOLATIONS has stale entries — drop them:\n"
            + "\n".join(stale),
        )


class HelperSanityTest(unittest.TestCase):
    """Empty-result sanity: catch a refactor that silently
    disables the layering check."""

    def test_layers_have_files(self) -> None:
        for layer in ("core", "services", "api", "cli"):
            self.assertGreater(
                len(_files_under(layer)), 0,
                f"src/media_stack/{layer}/ contains no .py files. "
                f"Update ROOT or rename the layer in this test.",
            )


if __name__ == "__main__":
    unittest.main()
