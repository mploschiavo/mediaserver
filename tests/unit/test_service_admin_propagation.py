"""Tests for ServiceAdminProvider + role-driven propagation.

Password reset on an admin (role.propagate_to_service_admins=true) must
push the new password to every single-login service registered as a
ServiceAdminProvider. Password reset on a regular user must NOT touch
those services.
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
from media_stack.core.auth.users.legacy_service_admin_adapter import (  # noqa: E402
    LegacyServiceAdminAdapter,
)
from media_stack.core.auth.users.provider import (  # noqa: E402
    ExternalUser, ProviderCapabilities, ProviderHealth,
)
from media_stack.core.auth.users.role_catalog import RoleCatalog  # noqa: E402
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper  # noqa: E402
from media_stack.core.auth.users.service_admin_provider import ServiceAdminHealth  # noqa: E402
from media_stack.core.auth.users.user_service import UserService  # noqa: E402
from media_stack.core.auth.users.user_store import UserStore  # noqa: E402

_CONTRACTS_ROLES = ROOT / "contracts" / "roles.yaml"


def _provider(name: str, is_source_of_truth: bool = False) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.capabilities = ProviderCapabilities(
        source_of_truth=is_source_of_truth,
        supports_groups=True, supports_password=True, supports_policy=False,
    )
    p.health_check.return_value = ProviderHealth(ok=True)
    p.list_users.return_value = []
    p.create_user.return_value = ExternalUser(external_id=name + "-id",
                                               username="x")
    p.set_password.return_value = None
    p.update_user.return_value = ExternalUser(external_id="x", username="x")
    return p


class LegacyServiceAdminAdapterTests(unittest.TestCase):
    def test_delegates_to_reset_fn_with_single_service_filter(self):
        reset_fn = MagicMock(return_value={
            "services": ["sonarr"], "errors": [],
        })
        adapter = LegacyServiceAdminAdapter("sonarr", reset_fn=reset_fn)
        adapter.set_admin_password("newpw")
        reset_fn.assert_called_once_with("newpw", target_services=["sonarr"])

    def test_raises_on_service_not_in_result(self):
        reset_fn = MagicMock(return_value={
            "services": [],  # sonarr was NOT reset
            "errors": ["sonarr: connection refused"],
        })
        adapter = LegacyServiceAdminAdapter("sonarr", reset_fn=reset_fn)
        with self.assertRaises(RuntimeError):
            adapter.set_admin_password("pw")

    def test_health_check_uses_probe_fn(self):
        probe = MagicMock(return_value=ServiceAdminHealth(ok=True, detail="up"))
        adapter = LegacyServiceAdminAdapter("qbit", reset_fn=MagicMock(),
                                             probe_fn=probe)
        h = adapter.health_check()
        self.assertTrue(h.ok)
        self.assertEqual(h.detail, "up")


class PropagationTests(unittest.TestCase):
    def _build(self, tmp: str, *, service_admins: list):
        store = UserStore(Path(tmp) / "users.json")
        audit = AuditLog(Path(tmp) / "audit.jsonl")
        return UserService(
            store=store,
            role_catalog=RoleCatalog(_CONTRACTS_ROLES),
            mapper=RolePolicyMapper(),
            providers=[_provider("authelia", is_source_of_truth=True),
                       _provider("jellyfin")],
            audit=audit,
            service_admins=service_admins,
        ), store

    def test_superadmin_password_propagates_to_service_admins(self):
        with tempfile.TemporaryDirectory() as tmp:
            qbit = MagicMock(); qbit.name = "qbittorrent"
            qbit.set_admin_password.return_value = None
            sonarr = MagicMock(); sonarr.name = "sonarr"
            sonarr.set_admin_password.return_value = None
            svc, store = self._build(tmp, service_admins=[qbit, sonarr])
            # Create an admin user
            admin = svc.create_user(
                email="a@x", username="admin", display_name="A",
                role_slug="superadmin",
            )
            qbit.set_admin_password.reset_mock()
            sonarr.set_admin_password.reset_mock()
            result = svc.reset_password(admin["id"], password="New-Str0ng_Pw!2026")

            qbit.set_admin_password.assert_called_once_with("New-Str0ng_Pw!2026")
            sonarr.set_admin_password.assert_called_once_with("New-Str0ng_Pw!2026")
            self.assertEqual(result["service_admins"]["qbittorrent"], "ok")
            self.assertEqual(result["service_admins"]["sonarr"], "ok")

    def test_regular_user_password_does_not_propagate(self):
        with tempfile.TemporaryDirectory() as tmp:
            qbit = MagicMock(); qbit.name = "qbittorrent"
            svc, store = self._build(tmp, service_admins=[qbit])
            adult = svc.create_user(
                email="j@x", username="jane", display_name="J",
                role_slug="adult",
            )
            qbit.set_admin_password.reset_mock()
            result = svc.reset_password(adult["id"], password="New-Str0ng_Pw!2026")

            qbit.set_admin_password.assert_not_called()
            self.assertEqual(result["service_admins"], {})

    def test_service_admin_errors_collected_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            qbit = MagicMock(); qbit.name = "qbittorrent"
            qbit.set_admin_password.side_effect = RuntimeError("qbit down")
            sonarr = MagicMock(); sonarr.name = "sonarr"
            sonarr.set_admin_password.return_value = None
            svc, store = self._build(tmp, service_admins=[qbit, sonarr])
            admin = svc.create_user(
                email="a@x", username="admin", display_name="A",
                role_slug="superadmin",
            )
            result = svc.reset_password(admin["id"], password="New-Str0ng_Pw!2026")
            self.assertIn("qbit down",
                          result["service_admins"]["qbittorrent"])
            self.assertEqual(result["service_admins"]["sonarr"], "ok")

    def test_audit_log_records_service_admin_propagation(self):
        with tempfile.TemporaryDirectory() as tmp:
            qbit = MagicMock(); qbit.name = "qbittorrent"
            qbit.set_admin_password.return_value = None
            svc, store = self._build(tmp, service_admins=[qbit])
            admin = svc.create_user(
                email="a@x", username="admin", display_name="A",
                role_slug="superadmin",
            )
            svc.reset_password(admin["id"], password="New-Str0ng_Pw!2026")
            recent = svc.audit_recent()
            reset_entry = next(e for e in reversed(recent)
                                if e["action"] == "reset_password")
            self.assertIn("service_admins", reset_entry["detail"])
            self.assertEqual(
                reset_entry["detail"]["service_admins"]["qbittorrent"], "ok",
            )


if __name__ == "__main__":
    unittest.main()
