"""Tests for the ``post-setup`` job.

Generated from ``bin/scaffold_job_test.py`` — fill in the four
scenarios below. The pattern is documented in
``tests/jobs/README.md``.

Cover:

1. Success path — happy run, ``status: ok``, expected side
   effects observed.
2. Service-unreachable path — upstream is down. The job must
   *not* raise; it should return ``status: error`` with a clear
   error string (or ``status: skipped`` if the contract allows).
3. Partial-skip path — some sub-tasks succeed, some are skipped
   (e.g., 3 of 5 *arrs configured because the others have no
   key yet). The job must not blow up.
4. Idempotency — running the job twice has the same effect as
   running it once. Re-running should be cheap and safe.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


JOB_NAME = "post-setup"


class _TodoMixin:
    def _todo(self, scenario: str) -> None:
        # Replace this with a real assertion once the fixture
        # set has been built. Keeping the placeholder is a soft
        # ratchet — the bare scaffold is a visible TODO in CI.
        self.skipTest(f"TODO: implement {scenario} for {JOB_NAME}")


class PostSetupSuccessTests(unittest.TestCase, _TodoMixin):

    def test_happy_path(self) -> None:
        """Happy run: every prerequisite present, side effects
        observed (e.g. config files updated, env populated)."""
        self._todo("success")


class PostSetupUnreachableTests(unittest.TestCase, _TodoMixin):

    def test_service_unreachable(self) -> None:
        """Upstream service is down — job must not raise; it
        should report the error in its result dict."""
        self._todo("service-unreachable")


class PostSetupPartialSkipTests(unittest.TestCase, _TodoMixin):

    def test_partial_skip(self) -> None:
        """Some sub-tasks succeed, some are skipped because their
        prerequisite isn't ready yet. Job must remain stable."""
        self._todo("partial-skip")


class PostSetupIdempotencyTests(unittest.TestCase, _TodoMixin):

    def test_running_twice_is_a_noop(self) -> None:
        """Run the job twice; the second run must produce the
        same observable state as the first."""
        self._todo("idempotency")


if __name__ == "__main__":
    unittest.main()
