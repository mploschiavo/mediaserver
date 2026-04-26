"""Unit tests for AuditChainVerifier."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.audit_chain_verifier import (
    AuditChainVerifier,
)
from media_stack.core.auth.users.audit_log import AuditLog


class AuditChainVerifierTests(unittest.TestCase):
    def _audit(self) -> tuple[AuditLog, Path]:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "audit.jsonl"
        return AuditLog(path), path

    def test_intact_chain_verifies(self):
        audit, _ = self._audit()
        audit.append(actor="alice", action="login", target="u1", result="ok")
        audit.append(actor="alice", action="update", target="u1", result="ok")
        v = AuditChainVerifier(audit_factory=lambda: audit)
        ok, detail = v.verify_once()
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertTrue(v.last_ok)
        self.assertGreater(v.last_checked_at, 0.0)

    def test_tampered_entry_detected(self):
        audit, path = self._audit()
        audit.append(actor="alice", action="login", target="u1", result="ok")
        audit.append(actor="alice", action="update", target="u1", result="ok")
        # Mutate the middle entry: flip result from ok to error.
        lines = path.read_text().splitlines()
        row = json.loads(lines[0])
        row["result"] = "error"
        lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")

        alerts: list[str] = []
        v = AuditChainVerifier(
            audit_factory=lambda: audit,
            alert_fn=lambda msg: alerts.append(msg),
        )
        ok, detail = v.verify_once()
        self.assertFalse(ok)
        self.assertIn("hash mismatch", detail)
        self.assertEqual(len(alerts), 1)
        self.assertGreater(v.first_tamper_at, 0.0)

    def test_empty_log_verifies(self):
        audit, _ = self._audit()
        v = AuditChainVerifier(audit_factory=lambda: audit)
        ok, _ = v.verify_once()
        self.assertTrue(ok)

    def test_alert_fn_errors_are_swallowed(self):
        audit, _ = self._audit()
        audit.append(actor="x", action="y", target="z", result="ok")
        # Break the chain.
        path = audit._path
        lines = path.read_text().splitlines()
        row = json.loads(lines[0])
        row["actor"] = "forged"
        lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")

        def _boom(_detail: str) -> None:
            raise RuntimeError("alert raised")

        v = AuditChainVerifier(audit_factory=lambda: audit, alert_fn=_boom)
        # Must not propagate the exception to the caller.
        ok, _ = v.verify_once()
        self.assertFalse(ok)

    def test_snapshot_exposes_state(self):
        audit, _ = self._audit()
        v = AuditChainVerifier(audit_factory=lambda: audit, interval_sec=120)
        v.verify_once()
        snap = v.snapshot()
        self.assertEqual(snap["interval_seconds"], 120)
        self.assertIn("last_checked_at", snap)
        self.assertIn("last_ok", snap)


if __name__ == "__main__":
    unittest.main()
