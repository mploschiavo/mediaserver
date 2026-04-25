"""Architecture layering ratchet.

Today's tree:

    src/media_stack/
    ├── api/         HTTP handlers + per-domain service modules
    ├── core/        platform primitives (auth, platforms, edge,
    │                  observability, notifications, events,
    │                  cli_common)
    ├── services/    domain logic + per-tech app adapters +
    │                 jobs/ (job framework, action/controller
    │                 handlers, controller runner)
    ├── adapters/    abstract adapter framework
    └── cli/         entry-point command modules (``*_main.py``)

Rules enforced:

1. ``core/`` is the platform/infrastructure layer; ``services/`` is
   the domain layer. Domain depends on platform; platform must
   NEVER depend on domain.
2. ``cli/`` is the entry-point layer. ``api/``, ``core/``, and
   ``services/`` must NEVER import from ``cli/``. CLI commands
   are leaves of the dependency graph — anything imported by
   non-cli code belongs in a proper home (``core/``,
   ``services/``, ``api/``).

Both reverse directions create import cycles waiting to fire.
Without these tests, the rules were convention only — the import
graph could grow upward without anyone noticing.

The ratchet shrinks; it must never grow. Each removed violation =
a one-line entry deleted from KNOWN_VIOLATIONS as the matching
refactor lands.

Recent moves under ADR-0002 Phase 16:
* ``cli/commands/job_framework.py`` → ``services/jobs/framework.py``
* ``cli/workflows/cli_common.py`` → ``core/cli_common.py``
* ``cli/commands/{action,controller}_handlers.py`` → ``services/jobs/``
* ``cli/commands/controller_runner.py`` → ``services/jobs/``
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

# {api, core, services} → cli/ violations: ZERO after v1.0.192.
# All previously-shared library code that lived under cli/ moved to
# its proper home (services/, core/) in v1.0.192:
#   * cli/commands/generate_envoy_config_main.py
#       → services/edge/envoy_config_generator.py
#   * cli/commands/generate_bootstrap_config.py
#       → services/jobs/bootstrap_config_generator.py
#   * cli/workflows/controller_component_resolver.py
#       → services/controller_component_resolver.py
#   * core/platforms/kubernetes/apply_scale_policy_main.py
#       collapsed into cli/commands/apply_scale_policy_main.py
#       (the old core/ path was a misplaced *_main.py)
# The dict remains as the explicit "allowed exceptions" mechanism
# so future additions can be tracked — but it ships empty.
KNOWN_CLI_VIOLATIONS: dict[str, set[str]] = {}


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
    """True if this exact (file, import) edge is on the core→services
    allowlist."""
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        return False
    bucket = KNOWN_VIOLATIONS.get(rel)
    if not bucket:
        return False
    return any(target in line for target in bucket)


def _line_imports_target(line: str, target: str) -> bool:
    """True if ``line`` is an import statement for ``target`` (a
    dotted module path like ``a.b.c``).

    Matches three import shapes:
    * ``from a.b.c import X``      (target == module)
    * ``from a.b import c``        (target == module, leaf-imported)
    * ``import a.b.c``             (target == module)

    The regex is bounded to a single line — never run it against
    a full file body or it will backtrack catastrophically.
    """
    if target in line:
        return True
    pkg, _, leaf = target.rpartition(".")
    if not (pkg and leaf):
        return False
    # ``from <pkg> import ... <leaf> ...`` — leaf may be aliased
    # or appear in a comma-separated import list.
    pattern = rf"from\s+{re.escape(pkg)}\s+import\s+[^\n]*\b{re.escape(leaf)}\b"
    return re.search(pattern, line) is not None


def _is_known_cli_violation(path: Path, line: str) -> bool:
    """True if this exact (file, import) edge is on the
    {api,core,services}→cli allowlist."""
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        return False
    bucket = KNOWN_CLI_VIOLATIONS.get(rel)
    if not bucket:
        return False
    return any(_line_imports_target(line, target) for target in bucket)


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


class NonCliLayersDoNotImportFromCliTest(unittest.TestCase):
    """``api/``, ``core/``, ``services/`` must not import from ``cli/``.

    CLI commands are leaves of the dependency graph — entry-points
    only. Anything imported by non-cli code belongs in a proper
    home (``core/``, ``services/``, or ``api/``). The reverse direction
    creates an import cycle and means the "CLI" module is actually a
    library wearing a CLI hat.
    """

    def test_no_non_cli_imports_from_cli(self) -> None:
        offenders: list[str] = []
        for layer in ("api", "core", "services"):
            for path in _files_under(layer):
                for line_no, line in _imports_from(path, "cli"):
                    if _is_known_cli_violation(path, line):
                        continue
                    rel = path.relative_to(ROOT.parent.parent)
                    offenders.append(f"  {rel}:{line_no}: {line}")
        self.assertEqual(
            offenders, [],
            "non-cli/ files must not import from cli/. "
            "CLI is the entry-point layer — extract the reusable "
            "code into services/ or core/ and have the cli/*_main.py "
            "call into it (post-ADR-0002 Phase 16).\n"
            + "\n".join(offenders),
        )


class KnownViolationsRatchetTest(unittest.TestCase):
    """Lock the count of allowed violations. The list shrinks when
    refactors land; it must NEVER grow. New cross-layer imports
    fail at the source — bumping this allowlist is rejected unless
    the new entry has a same-PR refactor plan."""

    EXPECTED_TOTAL = 3
    EXPECTED_CLI_TOTAL = 0

    def test_count_does_not_grow(self) -> None:
        actual = sum(len(targets) for targets in KNOWN_VIOLATIONS.values())
        self.assertLessEqual(
            actual, self.EXPECTED_TOTAL,
            f"KNOWN_VIOLATIONS grew from {self.EXPECTED_TOTAL} to "
            f"{actual}. New violations require a same-PR refactor "
            f"plan, not just an allowlist bump.",
        )

    def test_cli_count_does_not_grow(self) -> None:
        actual = sum(len(targets) for targets in KNOWN_CLI_VIOLATIONS.values())
        self.assertLessEqual(
            actual, self.EXPECTED_CLI_TOTAL,
            f"KNOWN_CLI_VIOLATIONS grew from {self.EXPECTED_CLI_TOTAL} "
            f"to {actual}. New cli/ imports from non-cli/ require a "
            f"same-PR refactor plan, not just an allowlist bump.",
        )

    def test_entries_actually_exist(self) -> None:
        """Stale entries hide drift — once a refactor removes an
        import, the allowlist entry must come out too."""
        stale: list[str] = []
        for allowlist_name, allowlist in (
            ("KNOWN_VIOLATIONS", KNOWN_VIOLATIONS),
            ("KNOWN_CLI_VIOLATIONS", KNOWN_CLI_VIOLATIONS),
        ):
            for rel_path, targets in allowlist.items():
                full = ROOT / rel_path
                if not full.is_file():
                    stale.append(f"  {allowlist_name}: {rel_path} (file not found)")
                    continue
                lines = full.read_text(encoding="utf-8").splitlines()
                for target in targets:
                    if any(_line_imports_target(line, target) for line in lines):
                        continue
                    stale.append(f"  {allowlist_name}: {rel_path}: {target} (not found)")
        self.assertEqual(
            stale, [],
            "Allowlist has stale entries — drop them:\n"
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
