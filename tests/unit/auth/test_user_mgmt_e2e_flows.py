"""End-to-end scenarios across the user-management stack.

These are integration-style tests: real UserStore + real AuditLog +
real RoleCatalog against in-memory mock providers. They exercise the
full orchestration through UserService — the same paths the HTTP API
uses — so a regression in any of the glue layers surfaces here.

Scenarios covered:
  1. Reconcile bootstrap admin from Authelia into controller DB; then
     reset admin's password propagates to service-admin providers.
  2. Create end-user, change role, reset password, delete — all state
     in audit log; delete revokes sessions before removing records.
  3. Superadmin password reset updates Authelia + every
     ServiceAdminProvider; adult password reset touches none of them.
  4. OIDC auto-provisioned Jellyseerr user picked up on second reconcile
     and linked to the existing controller row.
  5. Kids role: library names in catalog → IDs at apply-time via
     Jellyfin provider.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402
from media_stack.core.auth.users.models import UserState  # noqa: E402
from media_stack.core.auth.users.provider import (  # noqa: E402
    ExternalUser, ProviderCapabilities, ProviderHealth,
)
from media_stack.core.auth.users.role_catalog import RoleCatalog  # noqa: E402
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper  # noqa: E402
from media_stack.core.auth.users.user_service import UserService  # noqa: E402
from media_stack.core.auth.users.user_store import UserStore  # noqa: E402

_CONTRACTS_ROLES = ROOT / "contracts" / "roles.yaml"


class _FakeProvider:
    """In-memory UserProvider with a tiny record store. Behaves like a
    real backend: create assigns an id, list reflects current state,
    delete removes, update/set_password mutate.
    """

    def __init__(self, name: str, *, source_of_truth: bool = False,
                 auto_oidc: bool = False):
        self.name = name
        self.capabilities = ProviderCapabilities(
            source_of_truth=source_of_truth,
            supports_groups=True, supports_password=True,
            supports_policy=True,
            auto_provisions_on_login=auto_oidc,
        )
        self._users: dict[str, dict] = {}
        self._next_id = 1
        self.revoked: list[str] = []
        self.create_calls = 0
        self.delete_calls = 0

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True)

    def list_users(self) -> list[ExternalUser]:
        return [
            ExternalUser(external_id=ext_id, username=u["username"],
                         email=u["email"], groups=list(u.get("groups", [])),
                         extra=dict(u.get("extra", {})))
            for ext_id, u in self._users.items()
        ]

    def create_user(self, *, username: str, email: str, display_name: str,
                    password: str, groups: list[str],
                    policy: dict | None = None) -> ExternalUser:
        self.create_calls += 1
        ext_id = f"{self.name}-{self._next_id}"
        self._next_id += 1
        self._users[ext_id] = {
            "username": username, "email": email,
            "display_name": display_name, "password": password,
            "groups": list(groups), "policy": dict(policy or {}),
        }
        return ExternalUser(external_id=ext_id, username=username,
                             email=email, groups=list(groups))

    def update_user(self, external_id: str, *, display_name: str = "",
                    email: str = "",
                    groups: list[str] | None = None,
                    policy: dict | None = None) -> ExternalUser:
        u = self._users.get(external_id)
        if not u:
            raise RuntimeError(f"no user {external_id}")
        if groups is not None:
            u["groups"] = list(groups)
        if policy is not None:
            u["policy"] = dict(policy)
        return ExternalUser(external_id=external_id,
                             username=u["username"], email=u["email"])

    def delete_user(self, external_id: str) -> None:
        self.delete_calls += 1
        self._users.pop(external_id, None)

    def set_password(self, external_id: str, password: str) -> None:
        if external_id in self._users:
            self._users[external_id]["password"] = password

    def revoke_sessions(self, external_id: str) -> None:
        self.revoked.append(external_id)

    # Test helpers
    def seed(self, external_id: str, **fields) -> None:
        """Simulate a user showing up out-of-band (e.g. OIDC auto-provision
        or the bootstrap ``configure-auth`` job seeding admin).
        """
        self._users[external_id] = {
            "username": fields.get("username", external_id),
            "email": fields.get("email", ""),
            "display_name": fields.get("display_name", external_id),
            "password": fields.get("password", ""),
            "groups": fields.get("groups", []),
            "policy": fields.get("policy", {}),
            "extra": fields.get("extra", {}),
        }


def _build_svc(tmp: str, *, providers, service_admins=None):
    store = UserStore(Path(tmp) / "users.json")
    audit = AuditLog(Path(tmp) / "audit.jsonl")
    return UserService(
        store=store,
        role_catalog=RoleCatalog(_CONTRACTS_ROLES),
        mapper=RolePolicyMapper(),
        providers=providers,
        audit=audit,
        service_admins=service_admins or [],
    ), store, audit


class ReconcileAdminEndToEndTests(unittest.TestCase):
    """Scenario 1: bootstrap seeds admin in Authelia; admin appears via
    reconcile; password reset propagates to service-admin backends.
    """

    def test_bootstrap_admin_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _FakeProvider("authelia", source_of_truth=True)
            jellyfin = _FakeProvider("jellyfin")
            # Bootstrap seeded admin directly (no password yet)
            authelia.seed("admin", username="admin", email="admin@local",
                          groups=["admins"])

            qbit = MagicMock(); qbit.name = "qbittorrent"
            qbit.set_admin_password.return_value = None
            sonarr = MagicMock(); sonarr.name = "sonarr"
            sonarr.set_admin_password.return_value = None

            svc, store, audit = _build_svc(
                tmp, providers=[authelia, jellyfin],
                service_admins=[qbit, sonarr],
            )

            # Controller store is empty before reconcile
            self.assertEqual(svc.list_users(), [])
            report = svc.reconcile_report()
            authelia_diff = next(d for d in report if d["provider"] == "authelia")
            self.assertEqual(len(authelia_diff["orphans"]), 1)
            self.assertEqual(authelia_diff["orphans"][0]["username"], "admin")

            imported = svc.import_orphan(
                provider_name="authelia", external_id="admin",
                role_slug="superadmin", actor="reconcile",
            )
            self.assertEqual(imported["role_slug"], "superadmin")

            # Admin is now visible
            users = svc.list_users()
            self.assertEqual(len(users), 1)
            admin_id = users[0]["id"]

            # Reset admin password — propagates everywhere
            result = svc.reset_password(admin_id, password="S3cure_Admin-Pass!")
            self.assertEqual(result["providers"]["authelia"], "ok")
            # Service-admin propagation is now async; the response
            # returns ``"scheduled_async"`` and the actual HTTP
            # calls happen on a background daemon thread.
            self.assertEqual(result["service_admins"], "scheduled_async")
            import time as _t
            deadline = _t.monotonic() + 2.0
            while _t.monotonic() < deadline:
                if qbit.set_admin_password.called and sonarr.set_admin_password.called:
                    break
                _t.sleep(0.02)
            qbit.set_admin_password.assert_called_once_with("S3cure_Admin-Pass!")
            sonarr.set_admin_password.assert_called_once_with("S3cure_Admin-Pass!")
            # Authelia record has the new password (sync path)
            self.assertEqual(authelia._users["admin"]["password"], "S3cure_Admin-Pass!")


class RegularUserLifecycleTests(unittest.TestCase):
    """Scenario 2: full adult-user lifecycle — no service-admin propagation."""

    def test_create_change_role_reset_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _FakeProvider("authelia", source_of_truth=True)
            jellyfin = _FakeProvider("jellyfin")
            qbit = MagicMock(); qbit.name = "qbittorrent"

            svc, store, audit = _build_svc(
                tmp, providers=[authelia, jellyfin],
                service_admins=[qbit],
            )

            created = svc.create_user(
                email="j@x", username="jane", display_name="Jane",
                role_slug="adult", password="First-Str0ng_Pw!",
            )
            self.assertEqual(authelia._users["authelia-1"]["groups"],
                             ["family"])

            # Change role — Jellyfin policy updates too
            svc.set_role(created["id"], "teen")
            self.assertEqual(authelia._users["authelia-1"]["groups"],
                             ["family", "teens"])
            self.assertEqual(
                jellyfin._users["jellyfin-1"]["policy"]["MaxParentalRating"],
                13,
            )

            # Reset password — adult/teen has NO service-admin propagation
            qbit.set_admin_password.reset_mock()
            result = svc.reset_password(created["id"], password="Second-Str0ng_Pw!")
            self.assertEqual(result["providers"]["authelia"], "ok")
            self.assertEqual(result["service_admins"], {})
            qbit.set_admin_password.assert_not_called()

            # Delete — revokes sessions, then removes records
            svc.delete_user(created["id"])
            self.assertEqual(authelia.revoked, ["authelia-1"])
            self.assertEqual(jellyfin.revoked, ["jellyfin-1"])
            self.assertNotIn("authelia-1", authelia._users)
            self.assertNotIn("jellyfin-1", jellyfin._users)
            # Controller row soft-deleted
            stored = store.get(created["id"])
            self.assertEqual(stored.state, UserState.DELETED)

            # Audit trail has every action
            actions = [e["action"] for e in svc.audit_recent()]
            for expected in ("create_user", "set_role", "reset_password",
                             "delete_user"):
                self.assertIn(expected, actions)


class OidcAutoProvisionReconcileTests(unittest.TestCase):
    """Scenario 4: Jellyseerr user auto-provisioned at OIDC first login,
    then reconcile picks it up as an orphan and links it to an existing
    controller row.
    """

    def test_jellyseerr_orphan_linked_by_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _FakeProvider("authelia", source_of_truth=True)
            jellyseerr = _FakeProvider("jellyseerr", auto_oidc=True)

            svc, store, _ = _build_svc(
                tmp, providers=[authelia, jellyseerr],
            )

            # Admin creates jane through the controller — Authelia record
            # exists, Jellyseerr is deferred.
            created = svc.create_user(
                email="j@x", username="jane", display_name="J",
                role_slug="adult", password="Test-Str0ng_Pw!2026",
            )
            # Jellyseerr should NOT have been called at create time
            self.assertEqual(jellyseerr.create_calls, 0)

            # Jane logs into Jellyseerr via OIDC — it auto-provisions her
            jellyseerr.seed("42", username="jane", email="j@x")

            # Reconcile sweep: Jellyseerr has her but controller has no ref
            report = svc.reconcile_report()
            jellyseerr_diff = next(d for d in report
                                    if d["provider"] == "jellyseerr")
            self.assertEqual(len(jellyseerr_diff["orphans"]), 1)

            # Admin imports — links to existing controller row
            svc.import_orphan(
                provider_name="jellyseerr", external_id="42",
                role_slug="adult",
            )
            stored = store.get(created["id"])
            self.assertEqual(stored.provider_refs.get("jellyseerr"), "42")


class AdminPropagationIsolationTests(unittest.TestCase):
    """Scenario 3: confirm the propagation flag is the ONLY gate — roles
    without it never touch service-admin providers, even if their user
    has refs.
    """

    def test_guest_role_does_not_propagate(self):
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _FakeProvider("authelia", source_of_truth=True)
            qbit = MagicMock(); qbit.name = "qbittorrent"
            svc, _, _ = _build_svc(
                tmp, providers=[authelia], service_admins=[qbit],
            )
            guest = svc.create_user(
                email="g@x", username="guest", display_name="G",
                role_slug="guest", password="Test-Str0ng_Pw!2026",
            )
            qbit.set_admin_password.reset_mock()
            svc.reset_password(guest["id"], password="New-Str0ng_Pw!2026")
            qbit.set_admin_password.assert_not_called()

    def test_family_admin_does_not_propagate(self):
        """Only superadmin role has propagate flag in the shipped catalog."""
        with tempfile.TemporaryDirectory() as tmp:
            authelia = _FakeProvider("authelia", source_of_truth=True)
            qbit = MagicMock(); qbit.name = "qbittorrent"
            svc, _, _ = _build_svc(
                tmp, providers=[authelia], service_admins=[qbit],
            )
            fa = svc.create_user(
                email="fa@x", username="fa", display_name="FA",
                role_slug="family_admin", password="Fam-Admin_Pw!2026",
            )
            qbit.set_admin_password.reset_mock()
            svc.reset_password(fa["id"], password="New-Str0ng_Pw!2026")
            qbit.set_admin_password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
