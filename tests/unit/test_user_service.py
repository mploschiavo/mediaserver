"""Tests for UserService — orchestrates CRUD across multiple UserProviders."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
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
from media_stack.core.auth.users.user_service import UserService, UserServiceError  # noqa: E402
from media_stack.core.auth.users.user_store import UserStore  # noqa: E402

_CONTRACTS_ROLES = ROOT / "contracts" / "roles.yaml"


def _fake_source_of_truth(fail_on: str = ""):
    provider = MagicMock()
    provider.name = "authelia"
    provider.capabilities = ProviderCapabilities(
        source_of_truth=True, supports_groups=True,
        supports_password=True, supports_policy=False,
    )
    provider.health_check.return_value = ProviderHealth(ok=True)
    def _create(**kw):
        if fail_on == "create":
            raise RuntimeError("authelia blew up")
        return ExternalUser(external_id=kw["username"], username=kw["username"],
                             email=kw["email"], groups=kw.get("groups", []))
    provider.create_user.side_effect = _create
    provider.delete_user.return_value = None
    provider.update_user.return_value = ExternalUser(external_id="x", username="x")
    provider.set_password.return_value = None
    return provider


def _fake_secondary(name: str, fail_on: str = "", auto_oidc: bool = False):
    provider = MagicMock()
    provider.name = name
    provider.capabilities = ProviderCapabilities(
        source_of_truth=False, supports_groups=False,
        supports_password=True, supports_policy=True,
        auto_provisions_on_login=auto_oidc,
    )
    provider.health_check.return_value = ProviderHealth(ok=True)
    def _create(**kw):
        if fail_on == "create":
            raise RuntimeError(f"{name} create failed")
        return ExternalUser(external_id=f"{name}-id", username=kw["username"])
    provider.create_user.side_effect = _create
    provider.delete_user.return_value = None
    provider.update_user.return_value = ExternalUser(external_id="x", username="x")
    provider.set_password.return_value = None
    return provider


class UserServiceTests(unittest.TestCase):
    def _svc(self, tmp: str, providers: list[Any]) -> UserService:
        store = UserStore(Path(tmp) / "users.json")
        catalog = RoleCatalog(_CONTRACTS_ROLES)
        audit = AuditLog(Path(tmp) / "audit.log.jsonl")
        return UserService(store=store, role_catalog=catalog,
                           mapper=RolePolicyMapper(),
                           providers=providers, audit=audit)

    def test_create_user_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth()
            jf = _fake_secondary("jellyfin")
            svc = self._svc(tmp, [sot, jf])
            result = svc.create_user(
                email="jane@x", username="jane", display_name="Jane",
                role_slug="adult",
            )
            self.assertEqual(result["email"], "jane@x")
            self.assertTrue(result["generated_password"])
            self.assertEqual(result["secondary_results"]["jellyfin"], "ok")
            sot.create_user.assert_called_once()
            jf.create_user.assert_called_once()
            # Controller DB was updated with refs from both providers
            stored = svc.get_user(result["id"])
            self.assertEqual(stored["provider_refs"]["authelia"], "jane")
            self.assertEqual(stored["provider_refs"]["jellyfin"], "jellyfin-id")

    def test_create_fails_when_source_of_truth_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth(fail_on="create")
            jf = _fake_secondary("jellyfin")
            svc = self._svc(tmp, [sot, jf])
            with self.assertRaises(UserServiceError):
                svc.create_user(email="a@x", username="a", display_name="A",
                                role_slug="adult")
            # Jellyfin must NOT have been called, and the controller row is
            # soft-deleted (not hanging around).
            jf.create_user.assert_not_called()
            self.assertEqual(svc.list_users(), [])

    def test_create_continues_when_secondary_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth()
            jf = _fake_secondary("jellyfin", fail_on="create")
            svc = self._svc(tmp, [sot, jf])
            result = svc.create_user(email="a@x", username="a", display_name="A",
                                     role_slug="adult")
            self.assertEqual(result["email"], "a@x")
            self.assertIn("error", result["secondary_results"]["jellyfin"])

    def test_create_defers_oidc_auto_provisioned_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth()
            js = _fake_secondary("jellyseerr", auto_oidc=True)
            svc = self._svc(tmp, [sot, js])
            result = svc.create_user(email="a@x", username="a", display_name="A",
                                     role_slug="adult")
            self.assertEqual(result["secondary_results"]["jellyseerr"],
                             "deferred_oidc_first_login")
            js.create_user.assert_not_called()

    def test_create_unknown_role_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._svc(tmp, [_fake_source_of_truth()])
            with self.assertRaises(UserServiceError):
                svc.create_user(email="a@x", username="a", display_name="A",
                                role_slug="nope")

    def test_delete_user_calls_every_provider_with_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth()
            jf = _fake_secondary("jellyfin")
            svc = self._svc(tmp, [sot, jf])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            result = svc.delete_user(created["id"])
            self.assertEqual(result["providers"]["authelia"], "ok")
            self.assertEqual(result["providers"]["jellyfin"], "ok")
            sot.delete_user.assert_called_once_with("a")
            jf.delete_user.assert_called_once_with("jellyfin-id")
            # Controller row soft-deleted
            self.assertEqual(svc.list_users(), [])

    def test_set_role_updates_groups_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth()
            jf = _fake_secondary("jellyfin")
            svc = self._svc(tmp, [sot, jf])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            svc.set_role(created["id"], "kid")
            # Authelia update called with kid's sso_groups
            call_kwargs = sot.update_user.call_args.kwargs
            self.assertIn("family", call_kwargs["groups"])
            self.assertIn("kids", call_kwargs["groups"])
            # Jellyfin update called with policy, not groups
            jf_kwargs = jf.update_user.call_args.kwargs
            self.assertIn("policy", jf_kwargs)
            self.assertNotIn("groups", jf_kwargs)

    def test_reset_password_skips_providers_without_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth()
            jf = _fake_secondary("jellyfin")
            svc = self._svc(tmp, [sot, jf])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            result = svc.reset_password(created["id"])
            self.assertEqual(result["providers"]["authelia"], "ok")
            self.assertEqual(result["providers"]["jellyfin"], "ok")
            self.assertTrue(result["generated_password"])

    def test_reset_password_flips_env_seed_source_to_rotated(self):
        """Closes the env backdoor: once the admin rotates, the
        source field transitions to 'rotated' and BasicAuthVerifier
        stops honoring STACK_ADMIN_PASSWORD. Without this, 'reset
        my password' silently leaves the env password still valid."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._svc(tmp, [_fake_source_of_truth()])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            # Mark the user as env-seeded to simulate an admin-bootstrap row.
            svc._store.update(created["id"], source="env-seed")
            svc.reset_password(created["id"])
            user = svc._store.get(created["id"])
            self.assertEqual(
                user.source, "rotated",
                "reset_password did not transition source=env-seed "
                "to 'rotated' — env fallback stays open.",
            )

    def test_reset_password_also_flips_env_legacy_source(self):
        """Upgrade path: admin was linked from Authelia with
        source=env-legacy. Rotation must still close the backdoor."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._svc(tmp, [_fake_source_of_truth()])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            svc._store.update(created["id"], source="env-legacy")
            svc.reset_password(created["id"])
            self.assertEqual(
                svc._store.get(created["id"]).source, "rotated",
            )

    def test_reset_password_leaves_non_bootstrap_source_alone(self):
        """Regular users created via invite must not have their source
        overwritten on password reset — only bootstrap-source users
        transition."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._svc(tmp, [_fake_source_of_truth()])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            svc._store.update(created["id"], source="invite")
            svc.reset_password(created["id"])
            self.assertEqual(
                svc._store.get(created["id"]).source, "invite",
            )

    def test_set_state_marks_suspended(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._svc(tmp, [_fake_source_of_truth()])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            svc.set_state(created["id"], UserState.SUSPENDED)
            self.assertEqual(svc.get_user(created["id"])["state"], "suspended")

    def test_audit_log_records_every_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._svc(tmp, [_fake_source_of_truth()])
            created = svc.create_user(email="a@x", username="a",
                                       display_name="A", role_slug="adult")
            svc.set_role(created["id"], "kid")
            svc.reset_password(created["id"])
            svc.delete_user(created["id"])
            actions = [e["action"] for e in svc.audit_recent()]
            self.assertIn("create_user", actions)
            self.assertIn("set_role", actions)
            self.assertIn("reset_password", actions)
            self.assertIn("delete_user", actions)

    def test_provider_health_returns_per_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            sot = _fake_source_of_truth()
            jf = _fake_secondary("jellyfin")
            svc = self._svc(tmp, [sot, jf])
            health = svc.provider_health()
            names = {h["name"]: h for h in health}
            self.assertTrue(names["authelia"]["source_of_truth"])
            self.assertFalse(names["jellyfin"]["source_of_truth"])


if __name__ == "__main__":
    unittest.main()
