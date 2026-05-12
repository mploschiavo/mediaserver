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

    def test_duplicate_corruption_is_not_realerted(self):
        """The verifier must alert + log on first detection of a
        corruption signature, but NOT re-emit the same alert on
        every subsequent tick when the breakage is unchanged. Guards
        the k8s controller's 10-minute log spam we observed when a
        single chain break from 2026-04-27 produced an ERROR line on
        every verifier interval for weeks."""
        audit, path = self._audit()
        audit.append(actor="alice", action="login", target="u1", result="ok")
        audit.append(actor="alice", action="update", target="u1", result="ok")
        # Tamper.
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
        for _ in range(5):
            v.verify_once()
        # Exactly one alert despite five consecutive failing ticks.
        self.assertEqual(len(alerts), 1)
        # State remains "broken" — the first_tamper_at and last_detail
        # still surface the historical signal.
        self.assertFalse(v.last_ok)
        self.assertGreater(v.first_tamper_at, 0.0)

    def test_new_corruption_signature_realerts(self):
        """When the corruption signature *changes* (e.g. operator
        archived the broken log and a fresh race introduced a new
        break at a different entry), the verifier must alert again."""
        audit, path = self._audit()
        audit.append(actor="a", action="x", target="t1")
        audit.append(actor="a", action="x", target="t2")
        audit.append(actor="a", action="x", target="t3")
        # First break: tamper entry 0.
        lines = path.read_text().splitlines()
        row0 = json.loads(lines[0])
        row0["result"] = "tampered_0"
        lines[0] = json.dumps(row0, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")

        alerts: list[str] = []
        v = AuditChainVerifier(
            audit_factory=lambda: audit,
            alert_fn=lambda msg: alerts.append(msg),
        )
        v.verify_once()
        v.verify_once()  # dup — should not alert again
        self.assertEqual(len(alerts), 1)
        first_alert = alerts[0]

        # Revert entry 0; tamper entry 1 instead → new signature.
        row0_clean = {k: v for k, v in json.loads(
            path.read_text().splitlines()[0]).items()
            if k != "result"}
        row0_clean["result"] = "ok"
        new_lines = path.read_text().splitlines()
        new_lines[0] = json.dumps(
            row0_clean, sort_keys=True, separators=(",", ":"),
        )
        row1 = json.loads(new_lines[1])
        row1["result"] = "tampered_1"
        new_lines[1] = json.dumps(
            row1, sort_keys=True, separators=(",", ":"),
        )
        path.write_text("\n".join(new_lines) + "\n")
        v.verify_once()
        self.assertEqual(len(alerts), 2)
        self.assertNotEqual(alerts[1], first_alert)

    def test_recovery_logs_state_transition(self):
        """When the corrupted file is archived and a clean chain
        starts, the verifier logs a recovery line (so operators see
        the state transition) and stops re-alerting."""
        audit, path = self._audit()
        audit.append(actor="a", action="x", target="t1")
        # Break it.
        lines = path.read_text().splitlines()
        row = json.loads(lines[0])
        row["result"] = "tampered"
        lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")

        alerts: list[str] = []
        v = AuditChainVerifier(
            audit_factory=lambda: audit,
            alert_fn=lambda msg: alerts.append(msg),
        )
        v.verify_once()
        self.assertEqual(len(alerts), 1)
        self.assertFalse(v.last_ok)
        # Simulate operator-archive: empty the file (fresh chain).
        path.write_text("")
        v.verify_once()
        self.assertTrue(v.last_ok)
        # No new alert on recovery; only fresh corruption re-alerts.
        self.assertEqual(len(alerts), 1)
        # first_tamper_at preserved for forensics.
        self.assertGreater(v.first_tamper_at, 0.0)


if __name__ == "__main__":
    unittest.main()
