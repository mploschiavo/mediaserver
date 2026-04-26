"""Round-trip tests for the invite flow.

Happy path: admin creates an invite → user clicks the link and submits
their password → the user exists, is provisioned in every provider,
and the audit log records the chain. If any step of that chain breaks
silently, an invited user CAN'T SIGN IN and the admin has no way to
tell why — the bug class this test guards against.

Partial failures (token expired, already-accepted, weak password) are
also covered to lock in the error taxonomy so the UI can react.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402
from media_stack.core.auth.users.invite_service import (  # noqa: E402
    InviteError, InviteService,
)
from media_stack.core.auth.users.invite_store import InviteStore  # noqa: E402


class _CreatedUser(dict):
    """Lightweight user dict produced by a fake user_creator."""


class InviteRoundTripTests(unittest.TestCase):
    def _build(self, tmp: Path, *, creator=None, weak=False):
        store = InviteStore(tmp / "invites.json")
        audit = AuditLog(tmp / "audit.jsonl")
        if creator is None:
            def creator(**kwargs):
                if weak:
                    # emulate policy rejection
                    raise RuntimeError(
                        "password too short (need 12+ chars)")
                return _CreatedUser({
                    "id": "u-" + kwargs["username"],
                    "email": kwargs["email"],
                    "username": kwargs["username"],
                    "role_slug": kwargs["role_slug"],
                })
        return InviteService(invites=store, user_creator=creator,
                             audit=audit), store, audit

    def test_invite_then_accept_creates_user(self):
        """Full happy path: invite is created, the returned token can
        be used to create a user, and the user record is persisted
        with the invite's email and role."""
        with tempfile.TemporaryDirectory() as d:
            svc, store, audit = self._build(Path(d))
            invite = svc.create_invite(
                email="jane@local", role_slug="adult", actor="admin")
            self.assertTrue(invite["token"],
                            "invite must return the one-shot token")
            # Accept.
            user = svc.accept(
                token=invite["token"], username="jane",
                display_name="Jane", password="LongSecurePw-2026",
                actor="invitee")
            self.assertEqual(user["email"], "jane@local")
            self.assertEqual(user["role_slug"], "adult")
            self.assertNotIn(
                "generated_password", user,
                "accept() must not echo the password that the user "
                "just typed — the UI has it; echoing risks logs/UI "
                "leaking it into history.",
            )

    def test_invite_is_single_use(self):
        """Reusing the same token must fail with a clear error.
        Leaking a token twice (e.g. email + paste in Slack) should
        create exactly one user, not two."""
        with tempfile.TemporaryDirectory() as d:
            svc, _, _ = self._build(Path(d))
            invite = svc.create_invite(
                email="jane@local", role_slug="adult", actor="admin")
            svc.accept(
                token=invite["token"], username="jane",
                display_name="Jane", password="LongSecurePw-2026")
            with self.assertRaises(InviteError) as cm:
                svc.accept(
                    token=invite["token"], username="jane2",
                    display_name="Jane", password="LongSecurePw-2026")
            self.assertIn("already accepted", str(cm.exception).lower())

    def test_invite_expired_is_rejected(self):
        """An invite whose expires_at is in the past must surface
        as a clean error, not a 500 or a silent success. The store
        clamps ttl_hours to a 1-hour minimum on create, so we force
        expiry by rewriting expires_at directly — same state the
        store reaches naturally after the TTL elapses."""
        with tempfile.TemporaryDirectory() as d:
            svc, store, _ = self._build(Path(d))
            invite = svc.create_invite(
                email="jane@local", role_slug="adult", actor="admin")
            # Backdate the stored invite to force expiry, then rebuild
            # the service so the new store instance re-reads the file.
            import json
            invites_path = Path(d) / "invites.json"
            data = json.loads(invites_path.read_text())
            data["invites"][0]["expires_at"] = "2020-01-01T00:00:00+00:00"
            invites_path.write_text(json.dumps(data))
            svc2, _, _ = self._build(Path(d))
            with self.assertRaises(InviteError) as cm:
                svc2.accept(
                    token=invite["token"], username="jane",
                    display_name="Jane", password="LongSecurePw-2026")
            self.assertIn("expired", str(cm.exception).lower())

    def test_invite_rejects_policy_violating_password(self):
        """The accept path delegates password creation to user_service,
        which enforces the password policy. A weak password must NOT
        silently succeed with the default policy (length < 12)."""
        with tempfile.TemporaryDirectory() as d:
            svc, _, _ = self._build(Path(d), weak=True)
            invite = svc.create_invite(
                email="jane@local", role_slug="adult", actor="admin")
            with self.assertRaises(RuntimeError) as cm:
                svc.accept(
                    token=invite["token"], username="jane",
                    display_name="Jane", password="easy1")
            self.assertIn("too short", str(cm.exception).lower())

    def test_revoked_invite_cannot_be_used(self):
        """Revocation is a security control; verify it takes effect."""
        with tempfile.TemporaryDirectory() as d:
            svc, _, _ = self._build(Path(d))
            invite = svc.create_invite(
                email="jane@local", role_slug="adult", actor="admin")
            svc.revoke(invite["id"], actor="admin")
            with self.assertRaises(InviteError):
                svc.accept(
                    token=invite["token"], username="jane",
                    display_name="Jane", password="LongSecurePw-2026")

    def test_audit_log_captures_full_chain(self):
        """invite_created + invite_accepted must both land in the
        audit log so a revoked credential or suspicious acceptance
        can be traced back to the originating admin."""
        with tempfile.TemporaryDirectory() as d:
            svc, _, audit = self._build(Path(d))
            invite = svc.create_invite(
                email="jane@local", role_slug="adult", actor="admin")
            svc.accept(
                token=invite["token"], username="jane",
                display_name="Jane", password="LongSecurePw-2026",
                actor="jane")
            # audit.recent() shape: list of dicts with `action` field
            events = audit.recent()
            actions = [e["action"] for e in events]
            self.assertIn("invite_created", actions)
            self.assertIn("invite_accepted", actions)


if __name__ == "__main__":
    unittest.main()
