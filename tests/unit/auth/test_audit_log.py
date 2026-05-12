"""Tests for the hash-chained audit log."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
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

    def test_external_archive_resets_cache(self):
        """When the operator archives the audit file out-of-band
        (``mv audit.log.jsonl audit.log.jsonl.corrupted-…`` is the
        documented recovery path for a tamper-evident chain), the
        next ``append`` must start a fresh chain — prev_hash="" on
        the new entry — instead of continuing from the cached tail
        of the now-archived file. This guards the 2026-05-12 case
        where the operator archive left the class-level
        ``_LAST_HASH_CACHE`` pointing at the old file's last hash,
        so the first append after archive wrote ``prev_hash=…fe32…``
        when the entry should have been ``prev_hash=""``."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.log.jsonl"
            log = AuditLog(path)
            log.append(actor="a", action="x", target="t1")
            log.append(actor="a", action="x", target="t2")
            self.assertTrue(path.is_file())
            # Operator archive (rename out of the way).
            archived = path.with_suffix(".jsonl.corrupted")
            path.rename(archived)
            self.assertFalse(path.is_file())
            # Next append must start a fresh chain.
            entry = log.append(actor="a", action="x", target="t-fresh")
            self.assertEqual(entry.prev_hash, "")
            ok, detail = AuditLog(path).verify_chain()
            self.assertTrue(ok, f"new chain should verify: {detail}")

    def test_separate_instances_same_path_share_lock(self):
        """Two ``AuditLog`` instances pointing at the same file must
        produce a valid chain when concurrent threads call ``append``
        on them. This guards the 2026-04-27 race where the request
        thread + a background password-sync task each held their own
        per-instance lock and wrote two entries sharing the same
        ``prev_hash``."""
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.log.jsonl"
            log_a = AuditLog(path)
            log_b = AuditLog(path)
            # Seed an entry so both instances cache the same last_hash.
            log_a.append(actor="seed", action="seed", target="t0")

            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def worker(log: AuditLog, tag: str) -> None:
                try:
                    barrier.wait(timeout=2.0)
                    for i in range(20):
                        log.append(actor=tag, action="x", target=f"t{i}")
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=worker, args=(log_a, "main"))
            t2 = threading.Thread(target=worker, args=(log_b, "bg"))
            t1.start(); t2.start()
            t1.join(timeout=10); t2.join(timeout=10)
            self.assertEqual(errors, [])
            ok, detail = AuditLog(path).verify_chain()
            self.assertTrue(ok, f"chain broke under concurrent writers: {detail}")


if __name__ == "__main__":
    unittest.main()
