"""Ratchet: bootstrap is the full DAG — don't re-queue its jobs.

The 2026-04-21 incident: post-``bootstrap`` callback in
``controller_serve.py`` auto-queued 5 actions
(``configure-media-server``, ``post-setup``, ``envoy-config``,
``discover-indexers``, ``validate-credentials``) every time
bootstrap completed. All five already ran inside the bootstrap
DAG, so the auto-queue was pure duplicate work — and one of
them, ``discover-indexers``, takes ~14 minutes (per-indexer
Cardigann probes). End user sees the indexer-discovery phase
finish, then immediately watch it run again with no
explanation. Looks broken even though it's just wasteful.

Fix: bootstrap is now the full DAG, so the post-bootstrap
auto-queue is gone. Anything that genuinely needs to run AFTER
bootstrap should be added as a downstream contract job, not
re-queued from the action-completion handler.

This test pins both halves: the auto-queue list is gone, AND
``discover-indexers`` is reachable from the bootstrap DAG (so
removing the auto-queue doesn't accidentally orphan it).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_SERVE = ROOT / "src" / "media_stack" / "cli" / "commands" / "controller_serve.py"
_CORE_CONTRACT = ROOT / "contracts" / "services" / "core.yaml"


class NoRedundantPostBootstrapQueueRatchet(unittest.TestCase):

    def test_post_bootstrap_handler_does_not_queue_dag_jobs(self) -> None:
        text = _SERVE.read_text(encoding="utf-8")
        # The dead list, exact form. If any of these appear together
        # in a Python list literal anywhere in the file, that's the
        # auto-queue coming back.
        for forbidden_combo in (
            ('"discover-indexers"', '"post-setup"'),
            ("'discover-indexers'", "'post-setup'"),
        ):
            present = all(s in text for s in forbidden_combo)
            self.assertFalse(
                present,
                "controller_serve.py is auto-queuing both "
                "discover-indexers and post-setup after bootstrap. "
                "These already run inside the bootstrap DAG; queueing "
                "them again wastes ~14 minutes on a second "
                "discover-indexers pass. Add downstream jobs to the "
                "contract instead.",
            )
        # Belt-and-suspenders: no `action_trigger("discover-indexers"`
        # in the bootstrap-completion branch.
        m = re.search(
            r'action_name\s*==\s*"bootstrap"[^}]{0,1000}'
            r'action_trigger\(\s*"discover-indexers"',
            text, re.DOTALL,
        )
        self.assertIsNone(
            m,
            "discover-indexers is being explicitly fired from the "
            "bootstrap-completion handler — same redundancy.",
        )

    def test_discover_indexers_still_in_bootstrap_dag(self) -> None:
        """The fix is "stop double-queuing", not "stop running it".
        Confirm discover-indexers stays declared as a contract job
        so the bootstrap DAG still picks it up."""
        text = _CORE_CONTRACT.read_text(encoding="utf-8")
        self.assertIn(
            "discover-indexers:", text,
            "discover-indexers job dropped from the contract — "
            "removing the auto-queue would now orphan it. Restore "
            "the contract entry first.",
        )


if __name__ == "__main__":
    unittest.main()
