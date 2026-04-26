"""Unit tests for _ControllerRBAC — per-user authorization on mutations.

The controller's bearer-token scope gates what a *token* can do; this
RBAC layer gates what a *user* can do regardless of their token. A
user whose role has ``controller_admin=false`` is read-only even
when holding an admin-scope bearer.

Tests cover:
  - GET request always allowed (not a mutation)
  - POST allowed for controller_admin=True role
  - POST refused for controller_admin=False role
  - Unknown user → allowed (fallback so day-zero admin isn't locked out)
  - Trusted-proxy identity (Remote-User) looked up correctly
"""

from __future__ import annotations

import base64
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api import server as srv  # noqa: E402


@dataclass
class _Role:
    slug: str
    controller_admin: bool


@dataclass
class _User:
    username: str
    role_slug: str


class _FakeStore:
    def __init__(self, users: dict):
        self._by_username = users

    def get_by_username(self, name):
        return self._by_username.get(name)


class _FakeRoles:
    def __init__(self, roles: dict):
        self._by_slug = roles

    def get(self, slug):
        return self._by_slug.get(slug)


class _FakeService:
    def __init__(self, *, users, roles):
        self._store = _FakeStore(users)
        self._roles = _FakeRoles(roles)


class _FakeHeaders:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, name, default=""):
        return self._m.get(name, default)


class _FakeHandler:
    def __init__(self, *, command, auth_header="", client_ip="127.0.0.1",
                 extra_headers=None):
        self.command = command
        headers = {"Authorization": auth_header}
        headers.update(extra_headers or {})
        self.headers = _FakeHeaders(headers)
        self.client_address = (client_ip, 0)


def _basic(user, pw):
    raw = f"{user}:{pw}".encode()
    return "Basic " + base64.b64encode(raw).decode()


class ControllerRbacTests(unittest.TestCase):
    def _rbac_with(self, users, roles):
        rbac = srv._ControllerRBAC()
        fake = _FakeService(users=users, roles=roles)
        return rbac, mock.patch.object(
            srv, "_build_user_service", return_value=fake,
        )

    def test_get_always_allowed(self):
        rbac = srv._ControllerRBAC()
        h = _FakeHandler(command="GET")
        self.assertTrue(rbac.allows(h))

    def test_post_allowed_for_admin_role(self):
        rbac, patch_ctx = self._rbac_with(
            users={"alice": _User("alice", "superadmin")},
            roles={"superadmin": _Role("superadmin", True)},
        )
        with patch_ctx:
            h = _FakeHandler(
                command="POST",
                auth_header=_basic("alice", "anything"),
            )
            self.assertTrue(rbac.allows(h))

    def test_post_refused_for_nonadmin_role(self):
        rbac, patch_ctx = self._rbac_with(
            users={"bob": _User("bob", "adult")},
            roles={"adult": _Role("adult", False)},
        )
        with patch_ctx:
            h = _FakeHandler(
                command="POST",
                auth_header=_basic("bob", "anything"),
            )
            self.assertFalse(rbac.allows(h))

    def test_unknown_user_treated_as_admin(self):
        """Fallback: if the local user store doesn't have the basic-auth
        username (e.g. the STACK_ADMIN env-var account or day-zero),
        the mutation is allowed. Otherwise the very first boot would
        lock the admin out."""
        rbac, patch_ctx = self._rbac_with(
            users={},
            roles={"superadmin": _Role("superadmin", True)},
        )
        with patch_ctx:
            h = _FakeHandler(
                command="POST",
                auth_header=_basic("admin", "media-stack"),
            )
            self.assertTrue(rbac.allows(h))

    def test_trusted_proxy_identity_used_when_present(self):
        """When the request arrives through Authelia with Remote-User
        set, RBAC checks the role of THAT user, not any Authorization
        header (there may not even be one)."""
        rbac, patch_ctx = self._rbac_with(
            users={"carol": _User("carol", "adult")},
            roles={"adult": _Role("adult", False)},
        )
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"},
            clear=False,
        ), patch_ctx:
            h = _FakeHandler(
                command="POST",
                client_ip="10.1.2.3",
                extra_headers={"Remote-User": "carol"},
            )
            self.assertFalse(rbac.allows(h))


if __name__ == "__main__":
    unittest.main()
