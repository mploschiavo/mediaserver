"""Unit tests for the provider self-heal in user_write_service.

When a provider's set_password fails with "user not found" — e.g.
Authelia's users_database.yml was rebuilt, or the user was created
out-of-band — the write service now re-creates the user in that
provider with the current password instead of returning a silent
``error: user ... not found``. Without this, a password reset in
the dashboard appears to succeed but the user can't actually log
in through Authelia.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.password_policy import PasswordPolicy  # noqa: E402
from media_stack.core.auth.users.user_write_service import (  # noqa: E402
    UserWriteService,
)


@dataclass
class _FakeUser:
    id: str
    email: str
    username: str
    display_name: str
    role_slug: str
    provider_refs: dict
    password_history: list


@dataclass
class _FakeRole:
    slug: str
    propagate_to_service_admins: bool = False
    provider_payloads: dict = None


class _FakeProviderCaps:
    supports_password = True


class _MissingUserProvider:
    """Simulates Authelia when users_database.yml doesn't contain
    the user: set_password raises 'user not found', create_user
    succeeds. The self-heal must catch the failure and retry via
    create_user."""

    name = "authelia"
    capabilities = _FakeProviderCaps()

    def __init__(self):
        self.set_password_calls: list = []
        self.create_user_calls: list = []
        self._users: set = set()

    def set_password(self, external_id: str, password: str) -> None:
        self.set_password_calls.append((external_id, password))
        if external_id not in self._users:
            raise RuntimeError(f"user {external_id!r} not found")

    def create_user(self, *, username: str, email: str, display_name: str,
                    password: str, groups: list) -> None:
        self.create_user_calls.append({
            "username": username, "email": email,
            "display_name": display_name, "password": password,
            "groups": list(groups),
        })
        self._users.add(username)


class ProviderSelfHealTests(unittest.TestCase):
    def _make_service(self, provider) -> tuple[UserWriteService, _FakeUser]:
        store = MagicMock()
        user = _FakeUser(
            id="u-123", email="jane@local", username="jane",
            display_name="Jane", role_slug="adult",
            provider_refs={"authelia": "jane"},
            password_history=[],
        )
        store.get = MagicMock(return_value=user)
        store.update = MagicMock()
        roles = MagicMock()
        roles.get = MagicMock(return_value=_FakeRole(
            slug="adult",
            provider_payloads={"authelia": {"groups": ["users"]}},
        ))
        audit = MagicMock()
        svc = UserWriteService(
            store=store, role_catalog=roles, mapper=MagicMock(),
            providers=[provider], audit=audit,
            service_admins=[],
            password_policy=PasswordPolicy(min_length=4, require_class_count=1),
        )
        return svc, user

    def test_missing_user_is_healed_via_create_user(self):
        """set_password raising 'not found' triggers create_user with
        the record's email + display_name + role groups."""
        provider = _MissingUserProvider()
        svc, _ = self._make_service(provider)
        result = svc.reset_password("u-123", password="NewStrongPw-123")
        self.assertEqual(result["providers"]["authelia"], "healed",
                         f"expected 'healed', got {result['providers']}")
        self.assertEqual(len(provider.create_user_calls), 1)
        call = provider.create_user_calls[0]
        self.assertEqual(call["username"], "jane")
        self.assertEqual(call["email"], "jane@local")
        self.assertEqual(call["password"], "NewStrongPw-123")
        self.assertEqual(call["groups"], ["users"])

    def test_existing_user_takes_normal_path(self):
        """When the provider HAS the user, set_password succeeds and
        create_user is never called."""
        provider = _MissingUserProvider()
        provider._users.add("jane")
        svc, _ = self._make_service(provider)
        result = svc.reset_password("u-123", password="NewStrongPw-123")
        self.assertEqual(result["providers"]["authelia"], "ok")
        self.assertEqual(provider.create_user_calls, [])

    def test_non_not_found_error_still_surfaces(self):
        """Permission errors, disk full, etc. MUST NOT trigger
        create_user — only the specific 'not found' signal does."""
        provider = _MissingUserProvider()

        def angry_set(external_id, password):
            provider.set_password_calls.append((external_id, password))
            raise PermissionError("denied writing to users_database.yml")

        provider.set_password = angry_set
        svc, _ = self._make_service(provider)
        result = svc.reset_password("u-123", password="NewStrongPw-123")
        self.assertTrue(
            result["providers"]["authelia"].startswith("error:"),
            f"non-not-found error must not self-heal: {result!r}",
        )
        self.assertEqual(provider.create_user_calls, [])


if __name__ == "__main__":
    unittest.main()
