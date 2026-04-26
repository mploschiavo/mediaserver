"""Tests for session revocation on delete.

Deleting a user must revoke their active sessions before (or alongside)
deleting the account so a compromised/removed user can't keep streaming
with an existing token.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.audit_log import AuditLog  # noqa: E402
from media_stack.core.auth.users.provider import (  # noqa: E402
    ExternalUser, ProviderCapabilities, ProviderHealth,
)
from media_stack.core.auth.users.role_catalog import RoleCatalog  # noqa: E402
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper  # noqa: E402
from media_stack.core.auth.users.user_service import UserService  # noqa: E402
from media_stack.core.auth.users.user_store import UserStore  # noqa: E402

_CONTRACTS_ROLES = ROOT / "contracts" / "roles.yaml"


def _provider(name: str, source_of_truth: bool = False, has_revoke: bool = True):
    p = MagicMock()
    p.name = name
    p.capabilities = ProviderCapabilities(
        source_of_truth=source_of_truth,
        supports_groups=True, supports_password=True, supports_policy=False,
    )
    p.health_check.return_value = ProviderHealth(ok=True)
    p.list_users.return_value = []
    p.create_user.return_value = ExternalUser(external_id=f"{name}-id",
                                               username="x")
    p.delete_user.return_value = None
    p.update_user.return_value = ExternalUser(external_id="x", username="x")
    if has_revoke:
        p.revoke_sessions.return_value = None
    else:
        # Remove the attribute so UserService's getattr check falls through.
        del p.revoke_sessions
    return p


class SessionRevocationTests(unittest.TestCase):
    def _svc(self, tmp: str, providers: list) -> UserService:
        return UserService(
            store=UserStore(Path(tmp) / "users.json"),
            role_catalog=RoleCatalog(_CONTRACTS_ROLES),
            mapper=RolePolicyMapper(),
            providers=providers,
            audit=AuditLog(Path(tmp) / "audit.jsonl"),
        )

    def test_delete_revokes_sessions_before_delete_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _provider("authelia", source_of_truth=True)
            jf = _provider("jellyfin")
            svc = self._svc(tmp, [sot, jf])
            created = svc.create_user(email="j@x", username="jane",
                                       display_name="J", role_slug="adult")
            sot.revoke_sessions.reset_mock()
            jf.revoke_sessions.reset_mock()
            sot.delete_user.reset_mock()
            jf.delete_user.reset_mock()

            svc.delete_user(created["id"])

            sot.revoke_sessions.assert_called_once_with("authelia-id")
            jf.revoke_sessions.assert_called_once_with("jellyfin-id")
            sot.delete_user.assert_called_once_with("authelia-id")
            jf.delete_user.assert_called_once_with("jellyfin-id")

    def test_delete_tolerates_revoke_exception(self):
        """Session revocation is best-effort — a raise must not block delete."""
        with tempfile.TemporaryDirectory() as tmp:
            sot = _provider("authelia", source_of_truth=True)
            jf = _provider("jellyfin")
            jf.revoke_sessions.side_effect = RuntimeError("jf offline")
            svc = self._svc(tmp, [sot, jf])
            created = svc.create_user(email="j@x", username="jane",
                                       display_name="J", role_slug="adult")

            result = svc.delete_user(created["id"])

            # Delete still went through
            jf.delete_user.assert_called_once_with("jellyfin-id")
            self.assertEqual(result["providers"]["jellyfin"], "ok")

    def test_delete_works_with_provider_that_lacks_revoke(self):
        """Providers predating this feature (or with no session concept)
        must not break the delete flow.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sot = _provider("authelia", source_of_truth=True, has_revoke=False)
            svc = self._svc(tmp, [sot])
            created = svc.create_user(email="j@x", username="jane",
                                       display_name="J", role_slug="adult")

            result = svc.delete_user(created["id"])
            self.assertEqual(result["providers"]["authelia"], "ok")
            sot.delete_user.assert_called_once_with("authelia-id")


if __name__ == "__main__":
    unittest.main()
