"""Idempotence tests — running the same action twice must produce
byte-equal output (or at minimum, structurally equivalent output).

When a reconciler/generator isn't idempotent, it either:
  - Writes new random secrets on every run (breaking logins every
    time a config regen fires), or
  - Accumulates state (e.g. appending to a list every time), or
  - Rewrites users with different defaults on every run.

These tests lock in the invariant for the key generators.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.authelia_config_generator import (  # noqa: E402
    AutheliaConfigGenerator,
    AutheliaConfigOptions,
)


class AutheliaGeneratorIdempotenceTests(unittest.TestCase):
    """configure-auth runs on startup and on every auth-mode edit.
    If the output changes when inputs didn't, sessions die, users
    get locked out, and Envoy reloads for no reason."""

    def _opts(self) -> AutheliaConfigOptions:
        return AutheliaConfigOptions(
            base_domain="local",
            stack_subdomain="media-stack",
            gateway_host="apps.media-stack.local",
            gateway_port=443,
            internet_exposed=False,
            admin_username="admin",
            admin_email="admin@local",
            # Fixed secrets so we only measure the non-secret drift.
            session_secret="fixed-session-secret-" + ("x" * 20),
            storage_encryption_key="fixed-storage-key-" + ("x" * 20),
            jwt_secret="fixed-jwt-secret-" + ("x" * 20),
        )

    def test_configuration_is_byte_equal_across_runs(self):
        """Same options → same configuration.yml every time. A non-
        idempotent generator would show up here as a diff."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = (out / "configuration.yml").read_text()
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = (out / "configuration.yml").read_text()
        self.assertEqual(
            first, second,
            "Authelia configuration.yml changed across idempotent "
            "runs — a non-deterministic field is leaking into the "
            "output. Envoy would reload on every generator call.",
        )

    def test_users_database_is_stable_across_runs(self):
        """The merge path must be a fixed point: merge(admin, {admin})
        should equal merge(admin, {admin}) on the next call."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            AutheliaConfigGenerator(self._opts()).write_config(out)
            first = (out / "users_database.yml").read_text()
            AutheliaConfigGenerator(self._opts()).write_config(out)
            second = (out / "users_database.yml").read_text()
        self.assertEqual(first, second,
                         "users_database.yml changed on a no-op "
                         "regen — merge path is not idempotent.")


class RoutingOverrideIdempotenceTests(unittest.TestCase):
    """Saving the same routing config twice in a row should produce
    'no_changes' the second time and leave the file unmodified."""

    def test_second_identical_update_is_no_op(self):
        from media_stack.api.services.config._routing import (
            RoutingConfigService,
        )
        import os
        with tempfile.TemporaryDirectory() as d:
            profile_path = Path(d) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump({
                "metadata": {"name": "m"},
                "routing": {
                    "base_domain": "local",
                    "stack_subdomain": "media-stack",
                    "gateway_host": "apps.media-stack.local",
                    "gateway_port": 80,
                    "app_path_prefix": "/app",
                },
            }))
            orig = dict(os.environ)
            try:
                os.environ["BOOTSTRAP_PROFILE_FILE"] = str(profile_path)
                os.environ["CONFIG_ROOT"] = str(d)

                class _Profile:
                    def load(self):
                        return yaml.safe_load(profile_path.read_text()), profile_path

                    def media_server_id(self):
                        return ""
                svc = RoutingConfigService(_Profile())
                svc.update_routing({"gateway_port": 443})
                # Second call with same value.
                r = svc.update_routing({"gateway_port": 443})
                self.assertEqual(r.get("status"), "no_changes")
            finally:
                os.environ.clear()
                os.environ.update(orig)


if __name__ == "__main__":
    unittest.main()
