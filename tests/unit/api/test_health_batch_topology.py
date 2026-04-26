"""Tests for health history batch writes.

Dashboard-side topology/schedule rendering tests retired with
dashboard.html in v1.0.193 — the SPA UI under ``ui/`` owns those
assertions now."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.health as health_mod  # noqa: E402


class TestHealthHistoryBatchWrites(unittest.TestCase):
    """Verify buffered health history writes to reduce disk I/O."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmpfile.write(b"[]")
        self._tmpfile.close()
        self._orig_path = health_mod._HEALTH_HISTORY_PATH
        self._orig_flush = health_mod._HEALTH_HISTORY_LAST_FLUSH
        health_mod._HEALTH_HISTORY_PATH = Path(self._tmpfile.name)
        health_mod._HEALTH_HISTORY_BUFFER.clear()
        health_mod._HEALTH_HISTORY_LAST_FLUSH = time.time()

    def tearDown(self):
        health_mod._HEALTH_HISTORY_PATH = self._orig_path
        health_mod._HEALTH_HISTORY_LAST_FLUSH = self._orig_flush
        health_mod._HEALTH_HISTORY_BUFFER.clear()
        os.unlink(self._tmpfile.name)

    def test_single_append_buffers_only(self):
        """One append should NOT write to disk."""
        health_mod.append_health_history({"sonarr": {"status": "ok", "ms": 10}})
        on_disk = json.loads(Path(self._tmpfile.name).read_text())
        self.assertEqual(len(on_disk), 0)
        self.assertEqual(len(health_mod._HEALTH_HISTORY_BUFFER), 1)

    def test_buffer_accumulates(self):
        """Multiple appends accumulate in buffer."""
        for i in range(3):
            health_mod.append_health_history({"svc": {"status": "ok", "ms": i}})
        self.assertEqual(len(health_mod._HEALTH_HISTORY_BUFFER), 3)

    def test_flush_at_size_threshold(self):
        """Buffer flushes to disk when reaching FLUSH_SIZE entries."""
        for i in range(health_mod._HEALTH_HISTORY_FLUSH_SIZE):
            health_mod.append_health_history({"svc": {"status": "ok", "ms": i}})
        on_disk = json.loads(Path(self._tmpfile.name).read_text())
        self.assertGreater(len(on_disk), 0)
        self.assertEqual(len(health_mod._HEALTH_HISTORY_BUFFER), 0)

    def test_flush_at_interval(self):
        """Buffer flushes when interval has elapsed."""
        health_mod._HEALTH_HISTORY_LAST_FLUSH = time.time() - 60  # Pretend 60s ago
        health_mod.append_health_history({"svc": {"status": "ok", "ms": 1}})
        on_disk = json.loads(Path(self._tmpfile.name).read_text())
        self.assertEqual(len(on_disk), 1)

    def test_flush_clears_buffer(self):
        """After flush, buffer should be empty."""
        for i in range(health_mod._HEALTH_HISTORY_FLUSH_SIZE):
            health_mod.append_health_history({"svc": {"status": "ok", "ms": i}})
        self.assertEqual(len(health_mod._HEALTH_HISTORY_BUFFER), 0)

    def test_existing_history_preserved(self):
        """Flush should append to existing history, not overwrite."""
        Path(self._tmpfile.name).write_text(json.dumps([{"ts": 1, "services": {}}]))
        for i in range(health_mod._HEALTH_HISTORY_FLUSH_SIZE):
            health_mod.append_health_history({"svc": {"status": "ok", "ms": i}})
        on_disk = json.loads(Path(self._tmpfile.name).read_text())
        self.assertEqual(len(on_disk), 1 + health_mod._HEALTH_HISTORY_FLUSH_SIZE)

    def test_history_capped_at_1440(self):
        """History should not exceed 1440 entries after flush."""
        existing = [{"ts": i, "services": {}} for i in range(1440)]
        Path(self._tmpfile.name).write_text(json.dumps(existing))
        for i in range(health_mod._HEALTH_HISTORY_FLUSH_SIZE):
            health_mod.append_health_history({"svc": {"status": "ok", "ms": i}})
        on_disk = json.loads(Path(self._tmpfile.name).read_text())
        self.assertLessEqual(len(on_disk), 1440)

    def test_get_history_reads_flushed(self):
        """get_health_history should read data that was flushed."""
        for i in range(health_mod._HEALTH_HISTORY_FLUSH_SIZE):
            health_mod.append_health_history({"sonarr": {"status": "ok", "ms": 10}})
        result = health_mod.get_health_history()
        self.assertIn("sla", result)
        self.assertIn("entries", result)
        self.assertEqual(result["entries"], health_mod._HEALTH_HISTORY_FLUSH_SIZE)

    def test_sla_calculation(self):
        """SLA percentage should be correct after flush."""
        for i in range(health_mod._HEALTH_HISTORY_FLUSH_SIZE):
            status = "ok" if i < 4 else "error"
            health_mod.append_health_history({"sonarr": {"status": status, "ms": 10}})
        result = health_mod.get_health_history()
        sla = result.get("sla", {}).get("sonarr", {})
        self.assertEqual(sla.get("total"), health_mod._HEALTH_HISTORY_FLUSH_SIZE)
        self.assertEqual(sla.get("ok"), 4)


if __name__ == "__main__":
    unittest.main()
