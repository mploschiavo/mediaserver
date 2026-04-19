"""Round-trip tests for AutheliaConfigGenerator.write_config.

Regression guards for the 2026-04-19 bug: every call to
configure-auth silently wiped every non-admin user's password from
``users_database.yml`` because the generator overwrote the file
with ``{"users": {<admin only>}}``.

The bug surfaced as: admin resets jane's password in the UI, self-heal
correctly writes the row to users_database.yml, user tries to log in,
but in between a routine regen (triggered by any routing/auth edit)
clobbered the file. Jane's row disappears. Login fails. No error is
visible anywhere to the admin.

These tests write the DB twice in a row and assert that each regen
preserves every existing row and never clobbers a password.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.authelia_config_generator import (  # noqa: E402
    AutheliaConfigGenerator,
    AutheliaConfigOptions,
)


class UsersDatabasePreservationTests(unittest.TestCase):
    def _opts(self, **kw) -> AutheliaConfigOptions:
        defaults = dict(
            base_domain="local",
            stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
            gateway_port=443,
            internet_exposed=False,
            admin_username="admin",
            admin_email="admin@local",
        )
        defaults.update(kw)
        return AutheliaConfigOptions(**defaults)

    def _read_users(self, path: Path) -> dict:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data.get("users") or {}

    def test_regen_preserves_user_added_out_of_band(self):
        """The canonical bug: admin exists in the profile, jane was
        added by the controller's self-heal. A subsequent regen must
        NOT drop jane's row."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts())
            # First write — no existing file. Admin gets created.
            gen.write_config(out)
            # Simulate self-heal adding jane between regens.
            users_path = out / "users_database.yml"
            data = yaml.safe_load(users_path.read_text(encoding="utf-8"))
            data["users"]["jane"] = {
                "disabled": False,
                "displayname": "Jane",
                "email": "jane@local",
                "password": "$argon2id$v=19$m=65536,t=3,p=4$SALT$HASH",
                "groups": ["users"],
            }
            users_path.write_text(yaml.safe_dump(data), encoding="utf-8")

            # Second write — same config, triggers regen.
            gen.write_config(out)
            after = self._read_users(users_path)

            self.assertIn(
                "jane", after,
                "jane was dropped on regen — the generator is "
                "clobbering users_database.yml instead of merging.",
            )
            self.assertEqual(
                after["jane"]["password"],
                "$argon2id$v=19$m=65536,t=3,p=4$SALT$HASH",
                "jane's password was overwritten on regen — admin "
                "would be unable to log in after any routine edit.",
            )

    def test_regen_preserves_existing_admin_password(self):
        """When the profile has no admin_password_hash (the default
        path — admin is provisioned interactively), the generator
        emits an admin entry with no ``password`` key. A later regen
        MUST keep the password that the self-heal later wrote."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts())
            gen.write_config(out)
            users_path = out / "users_database.yml"
            # Simulate self-heal setting admin's password.
            data = yaml.safe_load(users_path.read_text(encoding="utf-8"))
            data["users"]["admin"]["password"] = "$argon2id$ADMINHASH"
            users_path.write_text(yaml.safe_dump(data), encoding="utf-8")

            gen.write_config(out)
            after = self._read_users(users_path)
            self.assertEqual(
                after["admin"]["password"], "$argon2id$ADMINHASH",
                "Admin's password was wiped on regen. An empty "
                "admin_password_hash in the profile must never "
                "clobber a real password on disk.",
            )

    def test_regen_applies_profile_changes_to_admin(self):
        """Merge must still PROPAGATE profile-driven changes (e.g.
        admin email, displayname). Only the password has the
        'don't-clobber-with-empty' rule."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts(
                admin_email="admin@old.example",
            ))
            gen.write_config(out)

            # Profile edit: email changed.
            gen2 = AutheliaConfigGenerator(self._opts(
                admin_email="admin@new.example",
            ))
            gen2.write_config(out)

            after = self._read_users(out / "users_database.yml")
            self.assertEqual(
                after["admin"]["email"], "admin@new.example",
                "Profile-driven admin fields didn't update on regen.",
            )

    def test_regen_without_existing_file_still_writes_admin(self):
        """Fresh install path: no users_database.yml exists; first
        write must produce one with admin present."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = AutheliaConfigGenerator(self._opts())
            gen.write_config(out)
            after = self._read_users(out / "users_database.yml")
            self.assertIn("admin", after)


class ConfigurationSecretsPreservationTests(unittest.TestCase):
    """Regression guards for 2026-04-19 Authelia crashloop:
    storage.encryption_key, session.secret, and
    identity_validation.reset_password.jwt_secret were
    regenerated on every ``write_config`` call. The encrypted
    rows in db.sqlite3 (which was written with a prior key)
    became undecryptable → 'the configured encryption key does
    not appear to be valid for this database' at startup → the
    whole auth gateway stays down.
    """

    def _opts(self, **kw):
        defaults = dict(
            base_domain="local",
            stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
            gateway_port=443,
            internet_exposed=False,
            admin_username="admin",
            admin_email="admin@local",
        )
        defaults.update(kw)
        return AutheliaConfigOptions(**defaults)

    def _read_config(self, path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def test_storage_encryption_key_survives_regen(self):
        """The one that would have prevented the crashloop: once
        Authelia's db.sqlite3 has rows encrypted with key K, every
        subsequent regen must keep emitting K. A new random key
        would brick startup."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = self._read_config(out / "configuration.yml")
            first_key = first["storage"]["encryption_key"]

            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = self._read_config(out / "configuration.yml")
            self.assertEqual(
                second["storage"]["encryption_key"], first_key,
                "storage.encryption_key changed on regen. "
                "Authelia's db.sqlite3 would now be undecryptable, "
                "producing the 2026-04-19 crashloop.",
            )

    def test_session_secret_survives_regen(self):
        """Rotating session.secret silently invalidates every
        logged-in user's cookie. Not fatal, but recurring surprise
        logouts after any dashboard edit."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first_secret = self._read_config(
                out / "configuration.yml")["session"]["secret"]
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second_secret = self._read_config(
                out / "configuration.yml")["session"]["secret"]
            self.assertEqual(first_secret, second_secret)

    def test_jwt_secret_survives_regen(self):
        """Password-reset jwt_secret rotation would invalidate
        any in-flight reset emails. Small blast radius but same
        root cause — regen must be idempotent for secrets."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = self._read_config(
                out / "configuration.yml")[
                "identity_validation"]["reset_password"]["jwt_secret"]
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = self._read_config(
                out / "configuration.yml")[
                "identity_validation"]["reset_password"]["jwt_secret"]
            self.assertEqual(first, second)

    def test_corrupt_existing_config_falls_back_to_fresh_secrets(self):
        """If the existing configuration.yml is unreadable, the
        generator must still write a valid file with fresh secrets
        rather than crash. Startup of a brand-new install passes
        through this path."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            out.mkdir(exist_ok=True)
            (out / "configuration.yml").write_text(
                "::: not valid yaml :::",
                encoding="utf-8",
            )
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = self._read_config(out / "configuration.yml")
            self.assertTrue(cfg["storage"]["encryption_key"])
            self.assertTrue(cfg["session"]["secret"])

    def test_placeholder_secrets_are_replaced_on_first_regen(self):
        """Bootstrap defaults seed configuration.yml with
        PLACEHOLDER_* values so Authelia can start before the
        controller ever runs. The first real regen must replace
        them with actual random secrets — otherwise the whole
        stack sits on well-known values, letting anyone with
        source access forge sessions."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "configuration.yml").write_text(
                "storage:\n"
                "  encryption_key: PLACEHOLDER_STORAGE_REPLACE_ON_FIRST_REGEN\n"
                "session:\n"
                "  secret: PLACEHOLDER_SESSION_REPLACE_ON_FIRST_REGEN\n"
                "identity_validation:\n"
                "  reset_password:\n"
                "    jwt_secret: PLACEHOLDER_JWT_REPLACE_ON_FIRST_REGEN\n",
                encoding="utf-8",
            )
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = self._read_config(out / "configuration.yml")
            self.assertNotIn(
                "PLACEHOLDER", cfg["storage"]["encryption_key"],
                "encryption_key still starts with PLACEHOLDER — the "
                "bootstrap seed leaked into the live config.",
            )
            self.assertNotIn(
                "PLACEHOLDER", cfg["session"]["secret"],
                "session.secret still starts with PLACEHOLDER.",
            )
            self.assertNotIn(
                "PLACEHOLDER",
                cfg["identity_validation"]["reset_password"]["jwt_secret"],
                "jwt_secret still starts with PLACEHOLDER.",
            )

    def test_legacy_change_this_secrets_are_replaced_on_regen(self):
        """Older installs may have the pre-4.38 'change-this-*'
        placeholder secrets on disk. Treat them the same as the
        new PLACEHOLDER_* sentinels."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "configuration.yml").write_text(
                "storage:\n"
                "  encryption_key: change-this-storage-key-with-32-or-more-chars\n"
                "session:\n"
                "  secret: change-this-session-secret-with-32-or-more-chars\n",
                encoding="utf-8",
            )
            AutheliaConfigGenerator(self._opts()).write_config(out)
            cfg = self._read_config(out / "configuration.yml")
            self.assertFalse(
                cfg["storage"]["encryption_key"].startswith("change-this"),
                "Legacy change-this-* encryption_key leaked through.",
            )


if __name__ == "__main__":
    unittest.main()
