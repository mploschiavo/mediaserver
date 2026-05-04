"""Tests for security: input validation, service names, webhook URLs, env vars, path traversal.

ADR-0007 Phase 2 Phase E retired the legacy ``handlers_post.handle()``
entry-point. The dispatch-driven validation cases in this file
(``TestServiceNameValidation`` / ``TestWebhookURLValidation`` /
``TestEnvVarPrefixValidation``) are now covered by the
``RouteDispatchHarness``-driven equivalents in:

* ``tests/unit/api/routes/test_post_admin_ops.py`` — service-name +
  ``/api/restart/{service}`` validation (TestRestartService).
* ``tests/unit/api/routes/test_webhooks_and_deferred.py`` — webhook
  URL scheme/netloc validation (TestWebhookRegister, TestSSRFAllowList).
* ``tests/unit/api/routes/test_post_user_resources.py`` — env-var
  prefix allowlist (TestEnvvarSet).

The snapshot path-traversal cases stay here because they exercise
the underlying service helper (``media_stack.api.services.ops``)
directly without going through the POST dispatcher — orthogonal to
the dispatch-layer migration.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.ops import diff_snapshots, get_snapshot_detail  # noqa: E402


class TestSnapshotPathTraversal(unittest.TestCase):
    def test_dotdot_in_detail(self):
        result = get_snapshot_detail("../../etc/passwd")
        self.assertIn("error", result)
        self.assertIn("Invalid", result["error"])

    def test_slash_in_detail(self):
        result = get_snapshot_detail("foo/bar.json")
        self.assertIn("error", result)

    def test_backslash_in_detail(self):
        result = get_snapshot_detail("foo\\bar.json")
        self.assertIn("error", result)

    def test_dotdot_in_diff_a(self):
        result = diff_snapshots("../evil", "snapshot-ok.json")
        self.assertIn("error", result)

    def test_dotdot_in_diff_b(self):
        result = diff_snapshots("snapshot-ok.json", "../evil")
        self.assertIn("error", result)

    def test_valid_snapshot_name_passes_validation(self):
        # Will fail with "not found" not "Invalid" — that means validation passed
        result = get_snapshot_detail("snapshot-20260409T120000.json")
        self.assertNotIn("Invalid", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
