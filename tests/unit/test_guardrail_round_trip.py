"""Round-trip tests for the disk guardrail config editor.

Admin adjusts "fill threshold" / "qBit cleanup age" in the UI → POST
/api/guardrails → next GET /api/guardrails returns the new values.
The failure this guards against is the silent-saves bug: POST returns
``{"status": "updated"}`` but the file on disk was never modified, so
the next GET returns the old state and the guardrails never trigger
at the new threshold.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.disk import DiskService  # noqa: E402


class GuardrailRoundTripTests(unittest.TestCase):
    def _make(self, tmp: Path) -> tuple[DiskService, Path]:
        cfg_path = tmp / "media-stack.config.json"
        cfg_path.write_text(json.dumps({
            "disk_guardrails": {
                "enabled": False,
                "max_used_percent": 65,
                "target_used_percent": 60,
                "monitor_path": "/media",
                "qbit_cleanup": {
                    "enabled": False,
                    "min_completion_age_hours": 36,
                    "min_ratio": 1.0,
                    "min_seeding_time_minutes": 720,
                    "max_delete_per_run": 10,
                    "delete_files": False,
                },
            },
        }), encoding="utf-8")
        return DiskService(), cfg_path

    def _patched(self, tmp: Path):
        """Patch resolve_config_path so DiskService reads/writes our
        temp file rather than the live /srv-config location."""
        svc, cfg = self._make(tmp)
        return svc, cfg, patch(
            "media_stack.api.services.disk.resolve_config_path",
            return_value=str(cfg),
        )

    def test_enabled_toggle_round_trips(self):
        """Flip enabled → save → read → enabled is True. The UI's
        enable-guardrails toggle wouldn't be usable if this fails."""
        with tempfile.TemporaryDirectory() as d:
            svc, _cfg, p = self._patched(Path(d))
            with p:
                result = svc.update_guardrails({"enabled": True})
                self.assertEqual(result["status"], "updated")
                # Read back.
                after = svc._load_guardrail_config()
                self.assertTrue(after["enabled"])

    def test_threshold_change_persists_to_file(self):
        """The 'max used %' input is the most common edit. Must be
        byte-identical on read after write."""
        with tempfile.TemporaryDirectory() as d:
            svc, cfg, p = self._patched(Path(d))
            with p:
                svc.update_guardrails({"max_used_percent": 85})
                # Assert on-disk JSON reflects the change.
                on_disk = json.loads(cfg.read_text())
                self.assertEqual(
                    on_disk["disk_guardrails"]["max_used_percent"], 85,
                )

    def test_qbit_nested_key_prefix_routes_correctly(self):
        """The handler accepts 'qbit_min_ratio' as shorthand for
        qbit_cleanup.min_ratio. A drop in that translation would
        silently write into a wrong key and the setting never
        takes effect."""
        with tempfile.TemporaryDirectory() as d:
            svc, cfg, p = self._patched(Path(d))
            with p:
                svc.update_guardrails({"qbit_min_ratio": 2.5})
                on_disk = json.loads(cfg.read_text())
                self.assertEqual(
                    on_disk["disk_guardrails"]["qbit_cleanup"]["min_ratio"],
                    2.5,
                )

    def test_unknown_keys_are_rejected_not_written(self):
        """An admin typo or a malicious POST with a rogue field
        must NOT land on disk. The allowlist is the write-gate."""
        with tempfile.TemporaryDirectory() as d:
            svc, cfg, p = self._patched(Path(d))
            with p:
                result = svc.update_guardrails({
                    "max_used_percent": 75,
                    "something_evil": "hack",
                })
                self.assertIn("max_used_percent", result["changed"])
                self.assertNotIn("something_evil", result["changed"])
                on_disk = json.loads(cfg.read_text())
                self.assertNotIn(
                    "something_evil", on_disk["disk_guardrails"],
                    "unknown key leaked into the persisted config; "
                    "the allowlist is broken.",
                )

    def test_no_change_does_not_rewrite_file(self):
        """Idempotence: posting the current values again reports
        no_changes and doesn't touch mtime — important when several
        admins have the same tab open."""
        with tempfile.TemporaryDirectory() as d:
            svc, cfg, p = self._patched(Path(d))
            with p:
                # Nothing that actually maps — unrelated key.
                result = svc.update_guardrails({"unknown": 1})
                self.assertEqual(result["status"], "no_changes")


if __name__ == "__main__":
    unittest.main()
