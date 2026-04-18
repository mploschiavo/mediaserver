"""Tests for BasicAuthVerifier — controller-auth reads from the user store
so password resets take effect without a pod restart.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from argon2 import PasswordHasher

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.basic_auth_verifier import BasicAuthVerifier  # noqa: E402
from media_stack.core.auth.users.role_catalog import RoleCatalog  # noqa: E402
from media_stack.core.auth.users.user_store import UserStore  # noqa: E402


def _write_users_db(tmp: Path, users: dict) -> Path:
    path = tmp / "authelia" / "users_database.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"users": users}))
    return path


def _write_role_catalog(tmp: Path) -> Path:
    path = tmp / "roles.yaml"
    path.write_text(yaml.safe_dump({
        "version": 1,
        "roles": {
            "superadmin": {
                "name": "Super Admin",
                "sso_groups": ["admins"],
                "propagate_to_service_admins": True,
            },
            "adult": {
                "name": "Adult",
                "sso_groups": ["family"],
                "propagate_to_service_admins": False,
            },
        },
    }))
    return path


class BasicAuthVerifierTests(unittest.TestCase):
    def _build(
        self,
        tmp_root: Path,
        *,
        users_db: dict | None = None,
        fallback_pw: str = "env-fallback",
    ) -> tuple[BasicAuthVerifier, UserStore, Path]:
        db_path = _write_users_db(tmp_root, users_db or {})
        roles_path = _write_role_catalog(tmp_root)
        store = UserStore(tmp_root / "users.json")
        verifier = BasicAuthVerifier(
            store=store,
            role_catalog=RoleCatalog(roles_path),
            users_db_path=db_path,
            fallback_username="admin",
            fallback_password=fallback_pw,
        )
        return verifier, store, db_path

    # --- fallback behavior (before any admin is reconciled) ---

    def test_fallback_accepts_env_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            v, _, _ = self._build(Path(tmp), fallback_pw="env-pw")
            self.assertTrue(v.verify("admin", "env-pw"))

    def test_fallback_rejects_wrong_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            v, _, _ = self._build(Path(tmp), fallback_pw="env-pw")
            self.assertFalse(v.verify("admin", "wrong"))

    def test_fallback_rejects_when_no_password_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            v, _, _ = self._build(Path(tmp), fallback_pw="")
            self.assertFalse(v.verify("admin", "anything"))

    # --- store-backed verification ---

    def test_verify_against_store_hash(self):
        hasher = PasswordHasher()
        new_pw_hash = hasher.hash("store-pw")
        users_db = {"admin": {"email": "a@x", "password": new_pw_hash,
                              "groups": ["admins"]}}
        with tempfile.TemporaryDirectory() as tmp:
            v, store, _ = self._build(Path(tmp), users_db=users_db,
                                       fallback_pw="env-pw")
            # Import admin into controller store with superadmin role
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin")
            store.update(u.id, provider_refs={"authelia": "admin"})
            # New password authenticates
            self.assertTrue(v.verify("admin", "store-pw"))
            # Old env password still works via fallback
            self.assertTrue(v.verify("admin", "env-pw"))
            # Random password fails on both paths
            self.assertFalse(v.verify("admin", "bogus"))

    def test_role_without_propagate_flag_blocked(self):
        """An 'adult' user must not authenticate to the controller UI even
        if their password hash matches.
        """
        hasher = PasswordHasher()
        hashed = hasher.hash("jane-pw")
        users_db = {"jane": {"email": "j@x", "password": hashed,
                             "groups": ["family"]}}
        with tempfile.TemporaryDirectory() as tmp:
            v, store, _ = self._build(Path(tmp), users_db=users_db,
                                       fallback_pw="")
            u = store.create(email="j@x", username="jane",
                             display_name="J", role_slug="adult")
            store.update(u.id, provider_refs={"authelia": "jane"})
            self.assertFalse(v.verify("jane", "jane-pw"))

    def test_suspended_admin_cannot_authenticate(self):
        from media_stack.core.auth.users.models import UserState
        hasher = PasswordHasher()
        hashed = hasher.hash("admin-pw")
        users_db = {"admin": {"email": "a@x", "password": hashed,
                              "groups": ["admins"]}}
        with tempfile.TemporaryDirectory() as tmp:
            v, store, _ = self._build(Path(tmp), users_db=users_db,
                                       fallback_pw="")
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin")
            store.update(u.id, provider_refs={"authelia": "admin"},
                         state=UserState.SUSPENDED)
            self.assertFalse(v.verify("admin", "admin-pw"))

    def test_store_hit_but_users_db_missing_falls_back(self):
        """If the controller store has admin but users_database.yml is gone,
        the verifier must still accept the env-var fallback.
        """
        with tempfile.TemporaryDirectory() as tmp:
            v, store, db_path = self._build(Path(tmp), users_db={},
                                             fallback_pw="env-pw")
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin")
            store.update(u.id, provider_refs={"authelia": "admin"})
            db_path.unlink()
            self.assertTrue(v.verify("admin", "env-pw"))

    def test_empty_username_or_password_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            v, _, _ = self._build(Path(tmp), fallback_pw="env-pw")
            self.assertFalse(v.verify("", "env-pw"))
            self.assertFalse(v.verify("admin", ""))


if __name__ == "__main__":
    unittest.main()
