"""Tests for the configure-auth job."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.configure_auth_job import configure_auth  # noqa: E402


def _ctx(profile: dict, config_root: str) -> SimpleNamespace:
    return SimpleNamespace(
        profile=profile,
        config_root=config_root,
        admin_username="admin",
        admin_password="pw",
    )


class ConfigureAuthJobTests(unittest.TestCase):
    def test_skipped_when_provider_not_authelia(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx({"auth": {"provider": "none"}}, tmp)
            result = configure_auth(ctx)
        self.assertIn("skipped", result)

    def test_skipped_when_no_auth_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx({}, tmp)
            result = configure_auth(ctx)
        self.assertIn("skipped", result)

    def test_writes_config_when_authelia(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = {
                "auth": {
                    "provider": "authelia",
                    "admin_password_hash": "$argon2id$...",
                    "admin_email": "admin@local",
                },
                "ingress": {"domain": "local", "subdomain": "media-stack"},
                "routing": {"gateway_host": "apps.media-stack.local", "gateway_port": 80},
            }
            ctx = _ctx(profile, tmp)
            result = configure_auth(ctx)
            self.assertEqual(result.get("provider"), "authelia")
            written = result.get("written") or []
            self.assertTrue(any("configuration.yml" in p for p in written))
            self.assertTrue(any("users_database.yml" in p for p in written))
            self.assertTrue((Path(tmp) / "authelia" / "configuration.yml").exists())


if __name__ == "__main__":
    unittest.main()
