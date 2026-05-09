"""Architecture ratchet pinning the absence of ``ensured_by:
type: lifecycle`` blocks on any production promise (ADR-0010
Phase 7).

What this catches
-----------------

Phase 7 collapsed every promise's ``ensured_by:`` from the legacy
``type: lifecycle`` indirection to ``type: job`` — the dispatcher
now routes through ``JobRunner.run(<job-name>)`` instead of
``_ensure_lifecycle`` reaching into a per-service Lifecycle class
by method-name. The wirer/lifecycle method bodies are preserved;
they're exposed as Job handlers via
``LifecycleHandlerAdapter.bind`` at module-import time so the
contract entry's ``handler:`` field references a flat callable.

If a future contract change re-introduces ``ensured_by: type:
lifecycle`` (e.g., copy-pasting from an older revision, or
reverting Phase 7 piecemeal), this ratchet fails before the
orchestrator's first tick — the dispatcher's
``LifecycleEnsurer`` branch produces a loud failure at runtime,
but the ratchet catches it earlier in CI.

Probes (``probe: type: lifecycle``) are NOT affected — the
lifecycle dispatch survives for probes (Phase 7 only collapses
ensurers).
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SERVICES_DIR = _REPO_ROOT / "contracts" / "services"


class _LifecycleEnsurerScanner:
    """Walks every ``contracts/services/*.yaml`` and collects every
    promise whose ``ensured_by:`` block declares
    ``type: lifecycle``."""

    def __init__(self, services_dir: Path) -> None:
        self._services_dir = services_dir

    def scan(self) -> list[tuple[str, str]]:
        """Return a list of ``(yaml-relpath, promise-id)`` for every
        promise whose ``ensured_by`` is the legacy lifecycle shape."""
        violations: list[tuple[str, str]] = []
        for yaml_path in sorted(self._services_dir.glob("*.yaml")):
            doc = self._load(yaml_path)
            if not doc:
                continue
            for promise in self._extract_promises(doc):
                pid = promise.get("id", "<unnamed>")
                ensured_by = promise.get("ensured_by")
                if self._is_lifecycle(ensured_by):
                    violations.append(
                        (yaml_path.name, pid),
                    )
        return violations

    @staticmethod
    def _load(path: Path) -> dict | None:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _extract_promises(doc: dict) -> list[dict]:
        plugin = doc.get("plugin", {})
        promises = plugin.get("promises", [])
        return promises if isinstance(promises, list) else []

    @staticmethod
    def _is_lifecycle(ensured_by) -> bool:
        return (
            isinstance(ensured_by, dict)
            and ensured_by.get("type") == "lifecycle"
        )


class NoLifecycleEnsurerInContractsRatchet(unittest.TestCase):

    def test_no_promise_uses_lifecycle_ensurer(self) -> None:
        scanner = _LifecycleEnsurerScanner(_SERVICES_DIR)
        violations = scanner.scan()
        msg_lines = [
            "ADR-0010 Phase 7 regression: promise(s) re-introduced "
            "the legacy ``ensured_by: type: lifecycle`` indirection. ",
            "Migrate each violation to:",
            "",
            "    ensured_by:",
            "      type: job",
            "      job_name: \"<service>:<verb-noun>\"",
            "",
            "and add a matching entry under ``plugin.jobs`` whose",
            "``handler:`` references the wirer/lifecycle module-level",
            "alias produced by ``LifecycleHandlerAdapter.bind``.",
            "",
            "Found:",
        ]
        for yaml_name, pid in violations:
            msg_lines.append(f"  {yaml_name}  ->  {pid}")
        self.assertEqual(violations, [], "\n".join(msg_lines))


if __name__ == "__main__":
    unittest.main()
