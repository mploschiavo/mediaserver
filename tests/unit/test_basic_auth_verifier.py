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
            # Import admin into controller store with superadmin role,
            # still tagged as an env-legacy seed so the fallback is
            # allowed until rotation.
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin",
                             source="env-legacy")
            store.update(u.id, provider_refs={"authelia": "admin"})
            # New password authenticates
            self.assertTrue(v.verify("admin", "store-pw"))
            # Old env password still works via fallback (pre-rotation)
            self.assertTrue(v.verify("admin", "env-pw"))
            # Random password fails on both paths
            self.assertFalse(v.verify("admin", "bogus"))

    def test_non_admin_role_can_authenticate_to_controller(self):
        """An 'adult' user with a valid password hash in the Authelia
        users_database MUST be able to authenticate — they're a
        real user who resets their password through the dashboard
        and expects to sign in on localhost:9100. Previously this
        test asserted the OPPOSITE behavior (blocking non-admins)
        which shipped as a production bug — jane could never log in
        to the controller even after a successful password reset."""
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
            self.assertTrue(
                v.verify("jane", "jane-pw"),
                "Non-admin user rejected despite valid credentials. "
                "The controller's localhost login is unusable for "
                "any role without propagate_to_service_admins.",
            )

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
        """If the controller store has admin (still on the bootstrap
        seed) but users_database.yml is gone, the verifier must still
        accept the env-var fallback so the operator can get back in."""
        with tempfile.TemporaryDirectory() as tmp:
            v, store, db_path = self._build(Path(tmp), users_db={},
                                             fallback_pw="env-pw")
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin",
                             source="env-legacy")
            store.update(u.id, provider_refs={"authelia": "admin"})
            db_path.unlink()
            self.assertTrue(v.verify("admin", "env-pw"))

    def test_empty_username_or_password_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            v, _, _ = self._build(Path(tmp), fallback_pw="env-pw")
            self.assertFalse(v.verify("", "env-pw"))
            self.assertFalse(v.verify("admin", ""))


class FallbackGateByAdminSourceTests(unittest.TestCase):
    """Phase 2: env fallback is active only while the admin row is
    still tagged with a bootstrap source. Once the admin rotates
    their password the row flips to ``source=rotated`` and the env
    backdoor is closed for good — no more 'STACK_ADMIN_PASSWORD
    still logs me in after I reset my password' surprise."""

    def _build(self, tmp: Path, fallback_pw="env-pw"):
        roles_path = _write_role_catalog(tmp)
        db_path = _write_users_db(tmp, {})
        store = UserStore(tmp / "users.json")
        v = BasicAuthVerifier(
            store=store, role_catalog=RoleCatalog(roles_path),
            users_db_path=db_path,
            fallback_username="admin",
            fallback_password=fallback_pw,
        )
        return v, store

    def test_fallback_active_when_admin_source_is_env_seed(self):
        """The fresh-deploy path: admin-bootstrap just created the
        row with source=env-seed. Env password MUST work so the
        operator can log in and rotate."""
        with tempfile.TemporaryDirectory() as tmp:
            v, store = self._build(Path(tmp))
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin",
                             source="env-seed")
            store.update(u.id, provider_refs={"authelia": "admin"})
            self.assertTrue(v.verify("admin", "env-pw"))

    def test_fallback_active_when_admin_source_is_env_legacy(self):
        """The upgrade path: an older deploy had admin in Authelia
        but no store row until Phase 1's link migration ran. Source
        is env-legacy. Env password must still work."""
        with tempfile.TemporaryDirectory() as tmp:
            v, store = self._build(Path(tmp))
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin",
                             source="env-legacy")
            store.update(u.id, provider_refs={"authelia": "admin"})
            self.assertTrue(v.verify("admin", "env-pw"))

    def test_fallback_disabled_when_admin_source_is_rotated(self):
        """The post-rotation state: admin changed their password via
        the UI → source=rotated. The env backdoor MUST be closed
        now. Attacker with a leaked STACK_ADMIN_PASSWORD=media-stack
        must get rejected — that is the entire point of Phase 2."""
        with tempfile.TemporaryDirectory() as tmp:
            v, store = self._build(Path(tmp))
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin",
                             source="rotated")
            store.update(u.id, provider_refs={"authelia": "admin"})
            self.assertFalse(
                v.verify("admin", "env-pw"),
                "Env fallback accepted the credential even though "
                "admin has rotated. The backdoor is still open.",
            )

    def test_fallback_disabled_for_unrecognized_source(self):
        """Any value other than env-seed/env-legacy disables the
        fallback. Conservative default — an unknown source value
        probably means someone tampered or a migration half-ran."""
        with tempfile.TemporaryDirectory() as tmp:
            v, store = self._build(Path(tmp))
            u = store.create(email="a@x", username="admin",
                             display_name="A", role_slug="superadmin",
                             source="invite")
            store.update(u.id, provider_refs={"authelia": "admin"})
            self.assertFalse(v.verify("admin", "env-pw"))

    def test_fallback_active_when_store_has_no_admin_row(self):
        """Cold-boot path: controller has just started and admin-
        bootstrap hasn't run yet. Env password MUST work or the
        first-boot experience is broken."""
        with tempfile.TemporaryDirectory() as tmp:
            v, _ = self._build(Path(tmp))
            self.assertTrue(v.verify("admin", "env-pw"))


if __name__ == "__main__":
    unittest.main()
