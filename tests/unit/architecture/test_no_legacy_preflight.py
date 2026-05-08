"""Architecture ratchet — ADR-0005 Phase 5c.1 wide cutover.

The legacy ``_run_preflights`` (in
``application/jobs/controller_handlers.py``) was deleted: every
service's API-key discovery now flows through the orchestrator's
promise model via ``orchestrator.satisfy_scope([…6 promises])``
called from ``services/apps/core/job_adapters.py::discover_api_keys``.

Two things must stay true going forward:

1. ``_run_preflights`` does NOT exist as an attribute on the
   controller-handlers module. A future contributor restoring it
   would silently double up the work — orchestrator dispatch +
   handler-spec dispatch would both run.

2. No production ``src/media_stack/**`` Python file references
   ``_run_preflights`` outside of historical-context comments
   (which are fine — they explain WHY it's gone).

The deleted symbol regression-test is the cheapest pin; the
grep-based check below catches accidental restoration via copy/paste
from the cutover commit's revert diff.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "media_stack"


class LegacyRunPreflightsDeletion(unittest.TestCase):
    def test_run_preflights_attribute_missing(self) -> None:
        from media_stack.application.jobs import controller_handlers
        self.assertFalse(
            hasattr(controller_handlers, "_run_preflights"),
            "_run_preflights resurrected on controller_handlers — "
            "the ADR-0005 Phase 5c.1 wide cutover removed it. "
            "discover_api_keys dispatches through "
            "orchestrator.satisfy_scope() instead. Restoring the "
            "legacy function would silently double up the work.",
        )

    def test_no_production_caller(self) -> None:
        """No ``src/media_stack`` file calls ``_run_preflights(``.
        Comments / docstrings mentioning the name are fine — only
        the call shape is forbidden."""
        offenders: list[str] = []
        # Match a function-call invocation (followed by ``(``), not
        # a textual mention. Allows ``_run_preflights`` in comments
        # / docstrings to point at the deletion.
        call_re = re.compile(r"\b_run_preflights\s*\(")
        for path in _SRC_ROOT.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                # Strip Python comments.
                if line.lstrip().startswith("#"):
                    continue
                if call_re.search(line):
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()[:120]}")
        self.assertFalse(
            offenders,
            "Production code calls _run_preflights — the ADR-0005 "
            "Phase 5c.1 wide cutover removed it. Use "
            "orchestrator.satisfy_scope([…api-key promises]) "
            "instead:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
