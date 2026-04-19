"""Guards the invariant: Authelia's admin password comes from
STACK_ADMIN_PASSWORD when no explicit hash is in the profile.

The 2026-04-19 bug: admin/media-stack worked through the controller
(which reads the env directly) but failed through Authelia (whose
users_database had a different argon2id hash). No test caught it
because nothing exercised the path that writes STACK_ADMIN_PASSWORD
into the Authelia config.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.configure_auth_job import ConfigureAuthJob  # noqa: E402


class ResolveAdminHashTests(unittest.TestCase):
    """Unit tests for ConfigureAuthJob._resolve_admin_hash."""

    def setUp(self):
        self._orig = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig)

    def test_explicit_hash_in_profile_wins(self):
        """Profile.auth.admin_password_hash overrides everything —
        lets an operator inject a known-good hash during bootstrap."""
        os.environ["STACK_ADMIN_PASSWORD"] = "ignored"
        result = ConfigureAuthJob()._resolve_admin_hash({
            "admin_password_hash": "$argon2id$v=19$...explicit...",
        })
        self.assertEqual(result, "$argon2id$v=19$...explicit...")

    def test_env_password_gets_hashed_when_profile_is_empty(self):
        """The common case: profile has no hash, env has
        STACK_ADMIN_PASSWORD=media-stack → generator gets a real
        argon2id hash. Without this, users_database.yml has an
        admin with NO password and Authelia refuses to start."""
        os.environ["STACK_ADMIN_PASSWORD"] = "media-stack"
        result = ConfigureAuthJob()._resolve_admin_hash({})
        self.assertTrue(
            result.startswith("$argon2id$"),
            f"expected an argon2id hash, got {result!r}. "
            "STACK_ADMIN_PASSWORD was not hashed — Authelia will "
            "see admin with no password and fail startup.",
        )

    def test_empty_env_returns_empty_preserving_existing(self):
        """If STACK_ADMIN_PASSWORD is unset (e.g. k8s deployment
        where creds live in a secret), return empty. The merge
        path in write_config preserves the existing password on
        disk rather than clobbering it."""
        os.environ.pop("STACK_ADMIN_PASSWORD", None)
        result = ConfigureAuthJob()._resolve_admin_hash({})
        self.assertEqual(result, "")

    def test_hash_round_trips_through_verify(self):
        """Hashing with argon2 should produce a string that
        Authelia's own verifier accepts. argon2-cffi is the same
        library Authelia uses."""
        from argon2 import PasswordHasher
        os.environ["STACK_ADMIN_PASSWORD"] = "media-stack"
        result = ConfigureAuthJob()._resolve_admin_hash({})
        PasswordHasher().verify(result, "media-stack")
        # If it threw, the test would fail. The verify() call is
        # the assertion.

    def test_existing_disk_password_blocks_env_reseed(self):
        """Regression for the silent-clobber bug: once admin has
        reset their password through the dashboard, a routine
        regen must not re-hash STACK_ADMIN_PASSWORD and overwrite
        the dashboard-set hash. Env is seed-only — it applies
        only when admin has no password on disk yet."""
        os.environ["STACK_ADMIN_PASSWORD"] = "media-stack"
        existing = "$argon2id$v=19$m=65536,t=3,p=4$DASHBOARD_SET"
        result = ConfigureAuthJob()._resolve_admin_hash(
            {}, existing_admin_pw=existing,
        )
        self.assertEqual(
            result, "",
            "Env-derived hash was emitted even though admin "
            "already has a password on disk. The next regen "
            "would clobber the dashboard-set password.",
        )

    def test_explicit_hash_still_overrides_existing_disk_password(self):
        """Operator-supplied admin_password_hash in the profile
        is a deliberate override (e.g. rotating a leaked hash)
        and MUST win even when the disk already has a password."""
        os.environ.pop("STACK_ADMIN_PASSWORD", None)
        result = ConfigureAuthJob()._resolve_admin_hash(
            {"admin_password_hash": "$argon2id$OPERATOR_OVERRIDE"},
            existing_admin_pw="$argon2id$OLD_ON_DISK",
        )
        self.assertEqual(result, "$argon2id$OPERATOR_OVERRIDE")


if __name__ == "__main__":
    unittest.main()
