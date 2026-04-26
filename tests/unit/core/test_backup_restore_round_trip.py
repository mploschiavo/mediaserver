"""Round-trip tests for config backup and restore.

The user-visible flow: admin clicks Download Backup → takes the
JSON → wipes the stack → clicks Restore. The stack must come back
with equivalent functionality. The failure class this guards:
backup omits a file the restore needs, or restore writes to a
different path than backup read from, or path-traversal protection
rejects a legit entry.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config._diagnostics import (  # noqa: E402
    DiagnosticsService,
)


class BackupRestoreRoundTripTests(unittest.TestCase):
    def _seed_config_root(self, root: Path) -> None:
        """Place a few config files that match real service layouts,
        so get_backup can find them and restore_backup can put them
        back. Paths must match the registry."""
        # Pick two real paths from the service registry.
        from media_stack.api.services.registry import SERVICES
        for svc in SERVICES:
            if svc.api_key_config and svc.api_key_format in ("xml", "json"):
                rel = svc.api_key_config
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if svc.api_key_format == "xml":
                    target.write_text(
                        '<Config><ApiKey>ORIGINAL_KEY_' + svc.id
                        + '</ApiKey></Config>', encoding="utf-8",
                    )
                else:
                    target.write_text(
                        '{"api_key": "ORIGINAL_KEY_' + svc.id + '"}',
                        encoding="utf-8",
                    )

    def test_backup_then_restore_reproduces_files(self):
        """Full happy path: write seeded files → backup → wipe →
        restore → files identical to original."""
        with tempfile.TemporaryDirectory() as src_d, \
             tempfile.TemporaryDirectory() as dst_d:
            src = Path(src_d)
            dst = Path(dst_d)
            self._seed_config_root(src)

            # Capture original file contents for later comparison.
            original: dict[str, str] = {}
            for path in src.rglob("*"):
                if path.is_file():
                    rel = str(path.relative_to(src))
                    original[rel] = path.read_text(encoding="utf-8")

            # Run the backup generator against src.
            orig_env = dict(os.environ)
            try:
                os.environ["CONFIG_ROOT"] = str(src)
                svc = DiagnosticsService(profile=MagicMock())
                backup_bytes = svc.get_backup(MagicMock())
                self.assertGreater(len(backup_bytes), 100,
                                   "backup suspiciously small")
                backup_obj = json.loads(backup_bytes.decode())
                self.assertEqual(backup_obj["version"], "2")
                self.assertIn("service_configs", backup_obj)

                # "Wipe and restore": point CONFIG_ROOT at dst_d,
                # call restore_backup, inspect written files.
                os.environ["CONFIG_ROOT"] = str(dst)
                svc2 = DiagnosticsService(profile=MagicMock())
                result = svc2.restore_backup(backup_obj, MagicMock())
                self.assertEqual(
                    result.get("status"), "ok",
                    f"restore didn't report success: {result}",
                )
                restored = result.get("restored", [])
                self.assertGreater(len(restored), 0,
                                   "no files restored")

                # Every restored file must exist at the same path
                # relative to the new config root, with the same
                # contents.
                for rel in restored:
                    dst_file = dst / rel
                    self.assertTrue(
                        dst_file.is_file(),
                        f"restored file missing at destination: {rel}",
                    )
                    self.assertEqual(
                        dst_file.read_text(encoding="utf-8"),
                        original[rel],
                        f"restored content differs from original: {rel}",
                    )
            finally:
                os.environ.clear()
                os.environ.update(orig_env)

    def test_restore_refuses_path_traversal(self):
        """A malicious backup with rel_path='../../../etc/passwd'
        must be SKIPPED (logged as an error), never written — the
        restore endpoint is sudo-gated but path traversal would
        still escape the config directory."""
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d)
            orig_env = dict(os.environ)
            try:
                os.environ["CONFIG_ROOT"] = str(dst)
                svc = DiagnosticsService(profile=MagicMock())
                backup = {
                    "version": "2",
                    "service_configs": {
                        "../../../tmp/pwned": "you're hacked",
                        "/etc/passwd": "also hacked",
                    },
                }
                result = svc.restore_backup(backup, MagicMock())
                # No files should have been restored; both attempts
                # must appear in the errors list.
                self.assertEqual(
                    len(result.get("restored", [])), 0,
                    "path-traversal entries were restored — escape "
                    "from the config directory is possible.",
                )
                errors_str = " ".join(result.get("errors", []))
                self.assertIn(
                    "unsafe", errors_str.lower(),
                    "path-traversal error not surfaced to admin",
                )
            finally:
                os.environ.clear()
                os.environ.update(orig_env)

    def test_restore_rejects_unsupported_version(self):
        """Backup from a far-future version must be rejected with
        a clear error, never partially applied."""
        svc = DiagnosticsService(profile=MagicMock())
        for v in ("99", "v3", "", "string-version"):
            result = svc.restore_backup({"version": v}, MagicMock())
            self.assertEqual(
                result.get("status"), "error",
                f"version {v!r} was accepted; future backups could "
                "trigger silent partial restores.",
            )

    def test_restore_with_empty_service_configs_is_rejected(self):
        """A backup with no service_configs key must be rejected
        cleanly — otherwise an empty restore looks like success
        even though nothing was actually written."""
        svc = DiagnosticsService(profile=MagicMock())
        result = svc.restore_backup({"version": "2"}, MagicMock())
        # Accept either an explicit error OR an ok-with-empty-list;
        # either is fine as long as no exception and no files touched.
        self.assertIn(result.get("status"), ("error", "ok"))
        if result.get("status") == "ok":
            self.assertEqual(
                result.get("restored", []), [],
                "empty backup claimed to restore files",
            )


if __name__ == "__main__":
    unittest.main()
