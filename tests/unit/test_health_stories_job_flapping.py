"""Ratchet #2 — job-flapping rule for health-stories.

The "discover-api-keys silently fails for hours" bug class showed
up as the same job erroring in batch after batch with nobody
catching it (because the dashboard tile counts had degraded to
zero, but the four core signals were all green).

This test pins the pure rule that scans recent run history and
emits a WARN-or-CRITICAL story per repeatedly-erroring job.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.health_stories import (  # noqa: E402
    job_flapping_stories,
)


def _batch(*results: tuple[str, str, str]) -> dict:
    """Build a minimal job-history batch.

    ``results`` is ``(name, status, error_text)`` tuples — empty
    error_text is fine for OK results.
    """
    return {
        "started_at": 0,
        "results": [
            {"name": n, "status": s, "error": e}
            for (n, s, e) in results
        ],
    }


class JobFlappingStoryTests(unittest.TestCase):

    def test_happy_path_no_errors_no_story(self) -> None:
        """Five clean batches → no flapping stories at all."""
        history = [
            _batch(("discover-api-keys", "ok", ""),
                   ("post-setup", "ok", ""))
            for _ in range(5)
        ]
        self.assertEqual(job_flapping_stories(history), [])

    def test_single_flap_below_threshold_no_story(self) -> None:
        """One transient error in 5 batches is below the floor —
        no story should be emitted (otherwise the panel would be
        permanently red after every rolling restart)."""
        history = [
            _batch(("post-setup", "error", "transient")),
            _batch(("post-setup", "ok", "")),
            _batch(("post-setup", "ok", "")),
            _batch(("post-setup", "ok", "")),
            _batch(("post-setup", "ok", "")),
        ]
        self.assertEqual(job_flapping_stories(history), [])

    def test_recurring_error_emits_warning_story(self) -> None:
        """Two errors out of five → one warning-severity story
        with the failure count and last error pinned in the body."""
        history = [
            _batch(("post-setup", "error", "boom v2")),
            _batch(("post-setup", "ok", "")),
            _batch(("post-setup", "error", "boom v1")),
            _batch(("post-setup", "ok", "")),
            _batch(("post-setup", "ok", "")),
        ]
        out = job_flapping_stories(history)
        self.assertEqual(len(out), 1)
        story = out[0]
        self.assertEqual(story["id"], "job-flapping:post-setup")
        self.assertEqual(story["severity"], "warning")
        self.assertIn("post-setup has failed 2", story["headline"])
        # last_error_text from the most-recent batch wins.
        self.assertIn("boom v2", story["description"])
        self.assertIn("/jobs", story["description"])

    def test_discover_api_keys_escalates_to_critical(self) -> None:
        """The named-and-shamed escalation path: when the failing
        job is ``discover-api-keys`` the operator-impact prefix
        is in the body and the severity bumps to critical."""
        history = [
            _batch(("discover-api-keys", "error", "401 from sonarr")),
            _batch(("discover-api-keys", "error", "401 from sonarr")),
            _batch(("discover-api-keys", "ok", "")),
            _batch(("discover-api-keys", "ok", "")),
            _batch(("discover-api-keys", "ok", "")),
        ]
        out = job_flapping_stories(history)
        self.assertEqual(len(out), 1)
        story = out[0]
        self.assertEqual(story["id"], "job-flapping:discover-api-keys")
        self.assertEqual(story["severity"], "critical")
        self.assertTrue(story["description"].startswith(
            "API keys are missing"
        ))
        self.assertIn("UI tile counts", story["description"])
        self.assertIn("401 from sonarr", story["description"])

    def test_only_last_five_batches_consulted(self) -> None:
        """Older batches must not pollute the count — a job
        that flapped weeks ago but has been clean for the last
        five runs should not still be flagged."""
        old_errors = [
            _batch(("post-setup", "error", "ancient")) for _ in range(5)
        ]
        recent_clean = [
            _batch(("post-setup", "ok", "")) for _ in range(5)
        ]
        # Newest first (matches the production prepend order).
        history = recent_clean + old_errors
        self.assertEqual(job_flapping_stories(history), [])

    def test_missing_error_text_falls_back(self) -> None:
        history = [
            _batch(("foo", "error", "")),
            _batch(("foo", "error", "")),
            _batch(("foo", "ok", "")),
        ]
        out = job_flapping_stories(history)
        self.assertEqual(len(out), 1)
        self.assertIn("no error text", out[0]["description"])

    def test_pure_no_io(self) -> None:
        """Empty/None input must short-circuit to []. Used as a
        proxy for "the function does not touch disk or env"."""
        self.assertEqual(job_flapping_stories([]), [])
        self.assertEqual(job_flapping_stories(None), [])  # type: ignore[arg-type]

    def test_production_jobs_dict_shape_works(self) -> None:
        """The job framework's ``_record_history`` writes
        ``{"jobs": {name: {status, ...}}}`` rather than the spec's
        ``results: [...]`` list. The rule must accept both shapes
        so the wired-up ``compose_live`` path actually fires."""
        history = [
            {"jobs": {"discover-api-keys": {"status": "error",
                                            "error": "boom"}}},
            {"jobs": {"discover-api-keys": {"status": "error",
                                            "error": "boom2"}}},
            {"jobs": {"discover-api-keys": {"status": "ok"}}},
        ]
        out = job_flapping_stories(history)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "critical")


if __name__ == "__main__":
    unittest.main()
