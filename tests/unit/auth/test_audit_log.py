"""Tests for the hash-chained audit log."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402


class AuditLogTests(unittest.TestCase):
    def _log(self, tmp: str) -> AuditLog:
        return AuditLog(Path(tmp) / "audit.log.jsonl")

    def test_append_creates_file_with_chained_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp)
            e1 = log.append(actor="alice", action="create_user", target="jane@x")
            e2 = log.append(actor="alice", action="set_role", target="jane@x")
            self.assertEqual(e1.prev_hash, "")
            self.assertTrue(e1.hash)
            self.assertEqual(e2.prev_hash, e1.hash)
            self.assertNotEqual(e1.hash, e2.hash)

    def test_verify_chain_passes_on_clean_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp)
            for i in range(3):
                log.append(actor="a", action="x", target=f"t{i}")
            ok, detail = log.verify_chain()
            self.assertTrue(ok, detail)

    def test_verify_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.log.jsonl"
            log = AuditLog(path)
            log.append(actor="a", action="x", target="t1")
            log.append(actor="a", action="x", target="t2")
            # Tamper: change target of first entry
            lines = path.read_text().splitlines()
            first = json.loads(lines[0])
            first["target"] = "TAMPERED"
            lines[0] = json.dumps(first)
            path.write_text("\n".join(lines) + "\n")
            ok, detail = log.verify_chain()
            self.assertFalse(ok)
            self.assertIn("mismatch", detail)

    def test_recent_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp)
            log.append(actor="a", action="create_user", target="x@x")
            log.append(actor="a", action="delete_user", target="y@x")
            log.append(actor="a", action="create_user", target="z@x")
            creates = log.recent(action_filter="create")
            self.assertEqual(len(creates), 2)
            target_hits = log.recent(target_filter="x@x")
            self.assertEqual(len(target_hits), 1)

    def test_detail_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp)
            log.append(actor="a", action="x", target="t",
                       detail={"role": "adult", "count": 3})
            entries = list(log.iter_entries())
            self.assertEqual(entries[0].detail, {"role": "adult", "count": 3})


if __name__ == "__main__":
    unittest.main()
