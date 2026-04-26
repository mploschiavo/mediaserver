"""Tests for UserReconciler — orphan/ghost diff and import."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402
from media_stack.core.auth.users.provider import (  # noqa: E402
    ExternalUser, ProviderCapabilities, ProviderHealth,
)
from media_stack.core.auth.users.reconcile import (  # noqa: E402
    ReconcileError, UserReconciler,
)
from media_stack.core.auth.users.user_store import UserStore  # noqa: E402


def _provider(name: str, users: list[ExternalUser],
              source_of_truth: bool = False) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.capabilities = ProviderCapabilities(source_of_truth=source_of_truth)
    p.health_check.return_value = ProviderHealth(ok=True)
    p.list_users.return_value = list(users)
    return p


class UserReconcilerDiffTests(unittest.TestCase):
    def _build(self, tmp: str, providers: list) -> tuple[UserStore, UserReconciler]:
        store = UserStore(Path(tmp) / "users.json")
        audit = AuditLog(Path(tmp) / "audit.jsonl")
        return store, UserReconciler(store=store, providers=providers, audit=audit)

    def test_empty_store_all_external_users_are_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _provider("authelia", [
                ExternalUser(external_id="admin", username="admin", email="a@x",
                             groups=["admins"]),
                ExternalUser(external_id="jane", username="jane", email="j@x",
                             groups=["family"]),
            ], source_of_truth=True)
            store, recon = self._build(tmp, [authelia])
            diffs = recon.diff()
        self.assertEqual(len(diffs), 1)
        d = diffs[0]
        self.assertEqual(d.provider, "authelia")
        self.assertEqual(d.matched, 0)
        self.assertEqual(len(d.orphans), 2)
        usernames = {o["username"] for o in d.orphans}
        self.assertEqual(usernames, {"admin", "jane"})

    def test_matched_users_do_not_appear_as_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _provider("authelia", [
                ExternalUser(external_id="jane", username="jane", email="j@x"),
            ])
            store, recon = self._build(tmp, [authelia])
            u = store.create(email="j@x", username="jane", display_name="Jane",
                             role_slug="adult")
            store.update(u.id, provider_refs={"authelia": "jane"})
            diffs = recon.diff()
        self.assertEqual(diffs[0].matched, 1)
        self.assertEqual(diffs[0].orphans, [])
        self.assertEqual(diffs[0].ghosts, [])

    def test_ghost_detected_when_local_ref_missing_from_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _provider("authelia", [])  # provider has nobody
            store, recon = self._build(tmp, [authelia])
            u = store.create(email="j@x", username="jane", display_name="J",
                             role_slug="adult")
            store.update(u.id, provider_refs={"authelia": "jane"})
            diffs = recon.diff()
        self.assertEqual(diffs[0].matched, 0)
        self.assertEqual(len(diffs[0].ghosts), 1)
        self.assertEqual(diffs[0].ghosts[0]["email"], "j@x")

    def test_provider_error_surfaces_in_ghosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _provider("authelia", [])
            authelia.list_users.side_effect = RuntimeError("boom")
            store, recon = self._build(tmp, [authelia])
            diffs = recon.diff()
        self.assertEqual(diffs[0].orphans, [])
        self.assertEqual(len(diffs[0].ghosts), 1)
        self.assertIn("boom", diffs[0].ghosts[0]["error"])


class UserReconcilerImportTests(unittest.TestCase):
    def _build(self, tmp: str, providers: list):
        store = UserStore(Path(tmp) / "users.json")
        audit = AuditLog(Path(tmp) / "audit.jsonl")
        return store, audit, UserReconciler(store=store, providers=providers, audit=audit)

    def test_import_creates_controller_row_with_provider_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _provider("authelia", [
                ExternalUser(external_id="admin", username="admin",
                             email="a@x", groups=["admins"],
                             extra={"displayname": "Media Admin"}),
            ])
            store, audit, recon = self._build(tmp, [authelia])
            result = recon.import_orphan(
                provider_name="authelia", external_id="admin",
                role_slug="superadmin", actor="alice",
            )
            self.assertEqual(result["email"], "a@x")
            self.assertEqual(result["role_slug"], "superadmin")
            self.assertEqual(result["provider_refs"]["authelia"], "admin")
            self.assertEqual(result["display_name"], "Media Admin")
            entries = audit.recent()
            self.assertEqual(entries[-1]["action"], "import_orphan")

    def test_import_links_siblings_by_email(self):
        """Admin exists in both Authelia and Jellyfin; importing one links both."""
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _provider("authelia", [
                ExternalUser(external_id="admin", username="admin", email="a@x"),
            ])
            jellyfin = _provider("jellyfin", [
                ExternalUser(external_id="jf-admin-id", username="admin",
                             email=""),
            ])
            store, audit, recon = self._build(tmp, [authelia, jellyfin])
            result = recon.import_orphan(
                provider_name="authelia", external_id="admin",
                role_slug="superadmin",
            )
        self.assertEqual(result["provider_refs"]["authelia"], "admin")
        self.assertEqual(result["provider_refs"]["jellyfin"], "jf-admin-id")

    def test_import_unknown_provider_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, recon = self._build(tmp, [_provider("authelia", [])])
            with self.assertRaises(ReconcileError):
                recon.import_orphan(
                    provider_name="nonexistent", external_id="x",
                    role_slug="adult",
                )

    def test_import_unknown_external_id_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, recon = self._build(tmp, [_provider("authelia", [])])
            with self.assertRaises(ReconcileError):
                recon.import_orphan(
                    provider_name="authelia", external_id="ghost",
                    role_slug="adult",
                )

    def test_relink_existing_user_updates_role_and_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _provider("authelia", [
                ExternalUser(external_id="jane", username="jane", email="j@x"),
            ])
            store, _, recon = self._build(tmp, [authelia])
            existing = store.create(
                email="j@x", username="jane", display_name="J",
                role_slug="adult",
            )
            result = recon.import_orphan(
                provider_name="authelia", external_id="jane",
                role_slug="teen",
            )
        # Same controller ID, updated role + ref
        self.assertEqual(result["id"], existing.id)
        self.assertEqual(result["role_slug"], "teen")
        self.assertEqual(result["provider_refs"]["authelia"], "jane")


class UserReconcilerUnlinkTests(unittest.TestCase):
    def test_unlink_ghost_drops_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.json")
            audit = AuditLog(Path(tmp) / "audit.jsonl")
            recon = UserReconciler(
                store=store, providers=[_provider("authelia", [])], audit=audit,
            )
            u = store.create(email="j@x", username="jane",
                             display_name="J", role_slug="adult")
            store.update(u.id, provider_refs={"authelia": "jane",
                                              "jellyfin": "jf-id"})
            recon.unlink_ghost(user_id=u.id, provider_name="authelia")
            after = store.get(u.id)
        self.assertNotIn("authelia", after.provider_refs)
        self.assertEqual(after.provider_refs.get("jellyfin"), "jf-id")

    def test_unlink_unknown_user_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.json")
            audit = AuditLog(Path(tmp) / "audit.jsonl")
            recon = UserReconciler(
                store=store, providers=[], audit=audit,
            )
            with self.assertRaises(ReconcileError):
                recon.unlink_ghost(user_id="nope", provider_name="authelia")


if __name__ == "__main__":
    unittest.main()
