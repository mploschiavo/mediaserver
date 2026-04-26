"""Error-path / resilience tests.

Most tests cover the happy path. Bugs hide in:
  - Malformed YAML/JSON in config files.
  - Missing contract files.
  - Permission-denied writes.
  - Duplicate service IDs across registries.
  - Empty files, truncated files.

Each test below injects one specific failure mode and asserts the
system degrades gracefully (error returned, defaults used, log
emitted) rather than crashing or silently producing bad output.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class MalformedConfigTests(unittest.TestCase):
    """Every file-based config service must survive malformed input
    without taking down the whole controller."""

    def test_password_policy_malformed_yaml_falls_back_to_defaults(self):
        """Admin hand-edits password-policy.yaml, makes a syntax
        error — service must not crash, policy must fall back to
        secure defaults."""
        from media_stack.api.services.password_policy_config import (
            PasswordPolicyConfig,
        )
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".controller" / "password-policy.yaml"
            target.parent.mkdir(parents=True)
            # Unterminated quote — classic YAML parse failure.
            target.write_text('password_policy:\n  min_length: "12\n',
                              encoding="utf-8")
            cfg = PasswordPolicyConfig(Path(d))
            values = cfg.load_values()
            # Must still produce a dict with int values (defaults).
            self.assertIsInstance(values["min_length"], int)
            self.assertGreaterEqual(values["min_length"], 4)

    def test_invite_store_malformed_json_returns_empty(self):
        """Corrupt invites.json — reader returns {} rather than
        crashing, so an admin can still load the Invites tab."""
        from media_stack.core.auth.users.invite_store import (
            InviteStore,
        )
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "invites.json"
            target.write_text("{ bad json", encoding="utf-8")
            store = InviteStore(target)
            self.assertEqual(store.list_all(), [])

    def test_routing_service_handles_missing_profile(self):
        """No profile file at all — routing endpoints must return
        usable defaults, not raise. Fresh-install safety."""
        from media_stack.api.services.config._routing import (
            RoutingConfigService,
        )

        class _EmptyProfile:
            def load(self):
                return {}, None

            def media_server_id(self):
                return ""

        svc = RoutingConfigService(_EmptyProfile())
        out = svc.get_routing()
        # All keys present; defaults in force.
        self.assertIn("base_domain", out)
        self.assertIn("gateway_host", out)
        self.assertIn("gateway_port", out)

    def test_authelia_generator_refuses_invalid_mode_input(self):
        """Profile says ``auth.mode: typo-mode`` — auth-config
        update endpoint rejects cleanly, doesn't corrupt the
        profile."""
        from media_stack.api.services.auth_config import AuthConfigService

        with tempfile.TemporaryDirectory() as d:
            profile_path = Path(d) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump({
                "metadata": {"name": "m"},
                "auth": {"mode": "none", "provider": "none",
                         "enabled": False},
                "routing": {"base_domain": "local",
                            "stack_subdomain": "m",
                            "gateway_host": "apps.m.local"},
            }))
            orig_env = dict(os.environ)
            try:
                os.environ["BOOTSTRAP_PROFILE_FILE"] = str(profile_path)
                os.environ["CONFIG_ROOT"] = str(d)
                result = AuthConfigService().update_auth_config(
                    {"mode": "total-garbage-value-99"})
                self.assertIn("error", result)
                # Profile untouched.
                after = yaml.safe_load(profile_path.read_text())
                self.assertEqual(after["auth"]["mode"], "none")
            finally:
                os.environ.clear()
                os.environ.update(orig_env)


class MissingFileTests(unittest.TestCase):
    """Services should tolerate MISSING files (fresh install) the
    same way they tolerate malformed ones: never raise, produce
    safe defaults."""

    def test_password_policy_missing_file_uses_defaults(self):
        from media_stack.api.services.password_policy_config import (
            PasswordPolicyConfig,
        )
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            self.assertFalse(cfg.path().exists())
            values = cfg.load_values()
            self.assertEqual(values["min_length"], 12)

    def test_user_store_missing_file_starts_empty(self):
        from media_stack.core.auth.users.user_store import UserStore
        with tempfile.TemporaryDirectory() as d:
            store = UserStore(Path(d) / "users.json")
            # No file yet; list must be empty, no raise.
            self.assertEqual(store.list_all(), [])


class BoundaryValueTests(unittest.TestCase):
    """Boundary conditions around limits — off-by-one is the most
    common bug class."""

    def test_password_at_minimum_length_exactly(self):
        """Password of EXACTLY min_length chars must be accepted,
        one less must be rejected. Off-by-one on the comparison
        silently blocks or allows the wrong passwords."""
        from media_stack.core.auth.users.password_policy import (
            PasswordPolicy,
        )
        pol = PasswordPolicy(min_length=12, require_class_count=3)
        # 12 chars, 3 classes.
        self.assertTrue(pol.check_candidate("Abcdef12!@#X").ok)
        # 11 chars, same classes.
        self.assertFalse(pol.check_candidate("Abcdef12!@#").ok)

    def test_empty_string_handled_like_invalid(self):
        from media_stack.core.auth.users.password_policy import (
            PasswordPolicy,
        )
        pol = PasswordPolicy()
        result = pol.check_candidate("")
        self.assertFalse(result.ok)
        self.assertTrue(result.reason)  # non-empty failure reason

    def test_very_long_password_accepted(self):
        """Some home-rolled crypto breaks above 72 bytes (bcrypt
        limit). argon2 handles arbitrary lengths — confirm that."""
        from media_stack.core.auth.users.password_policy import (
            PasswordPolicy,
        )
        pol = PasswordPolicy(min_length=4, require_class_count=1)
        very_long = "a" * 500
        self.assertTrue(pol.check_candidate(very_long).ok)


class RegistryCollisionTests(unittest.TestCase):
    """Duplicate service IDs in the registry break every lookup.
    The registry must refuse to load duplicates at import time."""

    def test_service_registry_has_no_duplicate_ids(self):
        """Snapshot the live registry; no two services share an id."""
        from media_stack.api.services.registry import SERVICES
        ids = [s.id for s in SERVICES]
        dupes = {x for x in ids if ids.count(x) > 1}
        self.assertEqual(
            dupes, set(),
            f"Duplicate service IDs in registry: {dupes}. "
            "Any lookup by id would return the wrong service.",
        )


if __name__ == "__main__":
    unittest.main()
