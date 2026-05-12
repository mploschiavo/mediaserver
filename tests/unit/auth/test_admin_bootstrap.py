"""Tests for the admin-bootstrap seeder.

The seeder closes the fresh-deploy UX gap where admin could log in
via STACK_ADMIN_PASSWORD fallback but never showed up in the Users
tab because nothing ever wrote an admin row into users.json.

Covers:
- idempotent run (calling twice = one admin, not two)
- skip when store already has a superadmin
- skip when STACK_ADMIN_PASSWORD is unset
- seed with custom username / email from env
- source field is tagged "env-seed" so the UI can surface a badge
- graceful degradation on provider failure (does not raise)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.admin_bootstrap import (  # noqa: E402
    AdminBootstrap,
    AdminBootstrapWeakPasswordError,
)
from media_stack.core.auth.users.models import (  # noqa: E402
    User,
    UserState,
)
from media_stack.core.auth.users.user_service_base import (  # noqa: E402
    UserServiceError,
)


def _fake_user(
    *, user_id="u1", username="admin", role="superadmin",
    state=UserState.ACTIVE, source="",
) -> User:
    return User(
        id=user_id, email=f"{username}@local", username=username,
        display_name="X", state=state, role_slug=role,
        created_at="t", updated_at="t", source=source,
    )


def _fake_service(users: list[User], sot_users=None) -> MagicMock:
    """Build a minimal UserService mock that admin-bootstrap will
    accept: has ._store with list_all + update, plus create_user.

    sot_users: list of objects with ``username`` + ``external_id``
    attributes — represents the rows the source-of-truth provider
    already holds (Authelia users_database.yml). Empty by default."""
    store = MagicMock()
    store.list_all.return_value = list(users)
    store.update.return_value = None
    store.create.return_value = User(
        id="linked-user-id", email="x@y", username="admin",
        display_name="X", state=UserState.ACTIVE,
        role_slug="superadmin", created_at="t", updated_at="t",
    )
    service = MagicMock()
    service._store = store
    service.create_user.return_value = {"id": "new-user-id"}
    sot = MagicMock()
    sot.name = "authelia"
    sot.list_users.return_value = list(sot_users or [])
    service._source_of_truth.return_value = sot
    return service


class AdminBootstrapTests(unittest.TestCase):

    def test_skips_when_superadmin_already_exists(self):
        """Once an admin is in the store, repeated boots must not
        create duplicate rows. This is the single invariant that
        makes the seeder safe to call on every UserService build."""
        service = _fake_service([_fake_user()])
        result = AdminBootstrap(env={"STACK_ADMIN_PASSWORD": "x"}).run(service)
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "existing_superadmin")
        service.create_user.assert_not_called()

    def test_seeds_when_store_is_empty_and_env_is_set(self):
        """The common fresh-deploy path: no users yet + env has the
        default password → admin row gets created with the env value
        and tagged source=env-seed for the UI to badge."""
        service = _fake_service([])
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "media-stack",
        }).run(service)
        self.assertEqual(result["action"], "seeded")
        self.assertEqual(result["source"], "env-seed")
        service.create_user.assert_called_once()
        call_kwargs = service.create_user.call_args.kwargs
        self.assertEqual(call_kwargs["username"], "admin")
        self.assertEqual(call_kwargs["email"], "admin@local")
        self.assertEqual(call_kwargs["role_slug"], "superadmin")
        self.assertEqual(call_kwargs["password"], "media-stack")
        # skip_policy_check MUST be set for the seed path — otherwise
        # the default "media-stack" password gets rejected by the
        # common-password blocklist and the whole install fails to
        # come up. Forced rotation (Phase 3) is where policy kicks in.
        self.assertTrue(
            call_kwargs.get("skip_policy_check"),
            "bootstrap must pass skip_policy_check=True so weak env "
            "passwords don't block a fresh deploy.",
        )
        # The row must be tagged so Phase 2 can gate fallback on it
        # and the dashboard can show "Seed (env)".
        service._store.update.assert_called_once_with(
            "new-user-id", source="env-seed",
        )

    def test_skips_when_no_credential(self):
        """If nothing in env AND nothing in the store, Phase 1 is a
        no-op. Phase 3 will later generate a random password here;
        the contract today is 'empty admin state, print nothing,
        leave the user to go through the dashboard setup wizard'."""
        service = _fake_service([])
        result = AdminBootstrap(env={}).run(service)
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "no_credential")
        service.create_user.assert_not_called()

    def test_env_username_and_email_override_defaults(self):
        """Operator overrides STACK_ADMIN_USERNAME / STACK_ADMIN_EMAIL
        — these must propagate into the seeded row. Otherwise an
        admin who set STACK_ADMIN_USERNAME=matthew gets a phantom
        'admin' user they didn't ask for."""
        service = _fake_service([])
        AdminBootstrap(env={
            "STACK_ADMIN_USERNAME": "matthew",
            "STACK_ADMIN_EMAIL": "m@example.com",
            "STACK_ADMIN_PASSWORD": "s3cret",
        }).run(service)
        call_kwargs = service.create_user.call_args.kwargs
        self.assertEqual(call_kwargs["username"], "matthew")
        self.assertEqual(call_kwargs["email"], "m@example.com")

    def test_suspended_admin_does_not_count_as_existing(self):
        """A soft-deleted or suspended admin must not block a new
        seed — otherwise 'disable the seed admin and re-seed' has
        to be done by hand-editing users.json."""
        service = _fake_service([
            _fake_user(state=UserState.SUSPENDED),
        ])
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "x",
        }).run(service)
        self.assertEqual(result["action"], "seeded")

    def test_non_superadmin_existing_user_does_not_block_seed(self):
        """A family_admin or adult user is not an admin — the seed
        must still run. The gate is specifically on ACTIVE SUPERADMIN."""
        service = _fake_service([
            _fake_user(role="adult"),
            _fake_user(user_id="u2", username="teen", role="teen"),
        ])
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "x",
        }).run(service)
        self.assertEqual(result["action"], "seeded")

    def test_create_user_failure_returns_error_not_raises(self):
        """A failure in the source-of-truth provider (e.g. Authelia
        isn't up yet during a cold boot) must not take down the
        whole controller. The seed just reports error and the
        fallback verifier keeps the admin logging in."""
        service = _fake_service([])
        service.create_user.side_effect = UserServiceError(
            "source-of-truth unreachable",
        )
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "x",
        }).run(service)
        self.assertEqual(result["action"], "error")
        self.assertIn("source-of-truth", result["error"])

    def test_links_existing_authelia_admin_instead_of_recreating(self):
        """Upgrade path: a deploy from before Phase 1 has admin in
        Authelia's users_database.yml with a valid hash but nothing
        in users.json. A fresh create_user would collide on the
        Authelia side ('user already exists'). The seeder must
        detect this and import the existing row into the store as
        source=env-legacy, leaving the provider's password
        untouched (has_password=True → don't re-seed)."""
        ext_admin = MagicMock()
        ext_admin.username = "admin"
        ext_admin.external_id = "admin"
        ext_admin.extra = {"has_password": True}
        service = _fake_service([], sot_users=[ext_admin])
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "media-stack",
        }).run(service)
        self.assertEqual(result["action"], "linked")
        self.assertEqual(result["source"], "env-legacy")
        self.assertFalse(result.get("password_seeded"))
        service.create_user.assert_not_called()
        service._source_of_truth.return_value.set_password.assert_not_called()
        service._store.create.assert_called_once()
        call_kwargs = service._store.create.call_args.kwargs
        self.assertEqual(call_kwargs["source"], "env-legacy")
        self.assertEqual(call_kwargs["role_slug"], "superadmin")

    def test_links_and_seeds_password_when_provider_row_has_none(self):
        """Fresh-install path: /defaults/users_database.yml ships
        with admin but no password hash (the stale 'media-dev'
        bcrypt seed was stripped to prevent shipping every install
        with a well-known credential). The seeder must write
        STACK_ADMIN_PASSWORD into Authelia so the first login
        works — otherwise Authelia crashloops with
        'Users.admin.users: non zero value required'."""
        ext_admin = MagicMock()
        ext_admin.username = "admin"
        ext_admin.external_id = "admin"
        ext_admin.extra = {"has_password": False}
        service = _fake_service([], sot_users=[ext_admin])
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "admin",
        }).run(service)
        self.assertEqual(result["action"], "linked")
        self.assertTrue(
            result.get("password_seeded"),
            "Legacy link path didn't seed the provider password "
            "even though has_password=False. Authelia will reject "
            "startup on the next restart.",
        )
        service._source_of_truth.return_value.set_password \
            .assert_called_once_with("admin", "admin")

    def test_idempotent_calls_only_seed_once(self):
        """The factory hook runs every time build() is called. This
        must never create a second admin on the second call."""
        service = _fake_service([])

        # First call: empty store → seed.
        bootstrap = AdminBootstrap(env={"STACK_ADMIN_PASSWORD": "x"})
        r1 = bootstrap.run(service)
        self.assertEqual(r1["action"], "seeded")

        # Simulate the admin row now being in the store.
        service._store.list_all.return_value = [_fake_user(source="env-seed")]
        # Reset the mock so we can assert no new create on round 2.
        service.create_user.reset_mock()

        r2 = bootstrap.run(service)
        self.assertEqual(r2["action"], "skipped")
        self.assertEqual(r2["reason"], "existing_superadmin")
        service.create_user.assert_not_called()


class WeakPasswordBlocklistTests(unittest.TestCase):
    """Phase 4: refuse to boot on a public deploy with a well-
    known default password. The blocklist is intentionally small
    and covers exactly the defaults that ship in our compose
    (``admin``) plus the obvious neighbors (``password``,
    ``changeme`` etc.) — we're not trying to be a password
    strength checker here, just a last-resort guard against
    shipping the convenience default to the internet."""

    def test_weak_password_warns_on_lan_deploy(self):
        """Local LAN: forced rotation closes the window quickly,
        so a warning is enough. Nothing raises."""
        service = _fake_service([])
        # Should not raise even though 'admin' is in the blocklist.
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "admin",
        }).run(service, internet_exposed=False)
        self.assertIn(result["action"], ("seeded", "linked"))

    def test_weak_password_fatal_on_internet_exposed_deploy(self):
        """Public deploy: admin/admin reachable from the internet
        gets owned by a random scanner within minutes. Refuse to
        boot. The operator must rotate STACK_ADMIN_PASSWORD to
        something off the blocklist before starting."""
        service = _fake_service([])
        with self.assertRaises(AdminBootstrapWeakPasswordError):
            AdminBootstrap(env={
                "STACK_ADMIN_PASSWORD": "admin",
            }).run(service, internet_exposed=True)
        service.create_user.assert_not_called()

    def test_strong_password_fine_on_internet_exposed(self):
        """The blocklist is a blocklist, not a policy — any
        password not on the list passes, regardless of its
        apparent entropy. Policy enforcement belongs elsewhere."""
        service = _fake_service([])
        result = AdminBootstrap(env={
            "STACK_ADMIN_PASSWORD": "correcthorsebatterystaple",
        }).run(service, internet_exposed=True)
        self.assertIn(result["action"], ("seeded", "linked"))

    def test_blocklist_is_case_insensitive(self):
        """Operators commonly capitalize ('Admin', 'Password') to
        think they've beaten a naive block. Normalize."""
        service = _fake_service([])
        with self.assertRaises(AdminBootstrapWeakPasswordError):
            AdminBootstrap(env={
                "STACK_ADMIN_PASSWORD": "Admin",
            }).run(service, internet_exposed=True)

    def test_blocklist_fires_on_existing_superadmin_boot(self):
        """A deploy that previously seeded an admin row but is
        rebooting with a now-known-weak STACK_ADMIN_PASSWORD must
        still hit the blocklist check. Pre-fix, the
        ``existing_superadmin`` shortcut returned before the check
        even ran — so an operator who rotated their secret to
        ``secret`` after first install never saw the WARN. Guards
        the 2026-05-12 follow-up where the live k8s deploy was
        booting with STACK_ADMIN_PASSWORD=secret and the guard was
        silent."""
        existing = _fake_user(role="superadmin", state=UserState.ACTIVE)
        service = _fake_service([existing])

        # internet_exposed=True must STILL refuse to boot even
        # though there's already a superadmin.
        with self.assertRaises(AdminBootstrapWeakPasswordError):
            AdminBootstrap(env={
                "STACK_ADMIN_PASSWORD": "secret",
            }).run(service, internet_exposed=True)

        # internet_exposed=False returns "skipped:existing_superadmin"
        # but the WARN log line fires. Sentinel captured via
        # assertLogs to prove the guard ran rather than bypassed.
        with self.assertLogs("media_stack", level="WARNING") as caught:
            result = AdminBootstrap(env={
                "STACK_ADMIN_PASSWORD": "secret",
            }).run(service, internet_exposed=False)
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "existing_superadmin")
        self.assertTrue(
            any("well-known weak credential" in r.getMessage()
                for r in caught.records),
            f"expected weak-credential WARN, got {[r.getMessage() for r in caught.records]}",
        )


if __name__ == "__main__":
    unittest.main()
