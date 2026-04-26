"""Round-trip tests for the auth mode switch.

Admin switches auth provider (basic → authelia, authelia → authentik,
authelia → none) in the Auth tab. The profile YAML must persist the
new mode AND fire the downstream triggers (configure-auth,
envoy-config) so Envoy's ext_authz wiring and Authelia's users DB
are regenerated. Without the action-trigger chain, the stored mode
says 'authentik' but the live Envoy still forwards to Authelia —
the "UI says one thing, stack does another" silent-drift bug.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.auth_config import AuthConfigService  # noqa: E402


class AuthModeSwitchRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._orig_env = {
            k: os.environ.get(k)
            for k in ("BOOTSTRAP_PROFILE_FILE", "CONFIG_ROOT")
        }

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _seed_profile(self, tmp: Path, mode: str = "basic") -> Path:
        path = tmp / "profile.yaml"
        path.write_text(yaml.safe_dump({
            "metadata": {"name": "media-stack"},
            "auth": {"enabled": mode != "none", "mode": mode,
                     "provider": mode},
            "routing": {
                "base_domain": "local",
                "stack_subdomain": "media-stack",
                "gateway_host": "apps.media-stack.local",
            },
        }), encoding="utf-8")
        return path

    def _svc(self, profile_path: Path) -> AuthConfigService:
        os.environ["BOOTSTRAP_PROFILE_FILE"] = str(profile_path)
        os.environ["CONFIG_ROOT"] = str(profile_path.parent)
        return AuthConfigService()

    def test_mode_switch_basic_to_authelia_persists(self):
        """The canonical flow: start with no SSO, turn on Authelia.
        The profile's auth.provider and auth.mode must both reflect
        the new mode after write."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed_profile(Path(d), mode="basic")
            svc = self._svc(p)
            svc.update_auth_config({"mode": "authelia"})
            profile = yaml.safe_load(p.read_text())
            self.assertEqual(profile["auth"]["provider"], "authelia")
            self.assertEqual(profile["auth"]["mode"], "authelia")
            self.assertTrue(profile["auth"]["enabled"])

    def test_mode_switch_to_none_disables_auth_cleanly(self):
        """Switching OFF must flip enabled:false AND remove the
        middleware config that would otherwise leave a stale
        reference for Envoy to pick up."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed_profile(Path(d), mode="authelia")
            svc = self._svc(p)
            svc.update_auth_config({"mode": "none"})
            profile = yaml.safe_load(p.read_text())
            self.assertEqual(profile["auth"]["provider"], "none")
            self.assertFalse(profile["auth"]["enabled"])
            self.assertNotIn(
                "middleware", profile.get("auth", {}),
                "middleware key leftover after switching to none — "
                "Envoy's config generator will still wire ext_authz.",
            )

    def test_mode_change_fires_envoy_config_action(self):
        """Without an envoy-config action trigger, the live Envoy
        keeps its old ext_authz wiring even though the profile
        changed. This is the silent-drift failure mode."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed_profile(Path(d), mode="basic")
            svc = self._svc(p)
            trigger = MagicMock()
            svc.update_auth_config(
                {"mode": "authelia"}, action_trigger=trigger)
            fired = [c[0][0] for c in trigger.call_args_list]
            self.assertIn(
                "envoy-config", fired,
                "auth mode edit didn't queue envoy-config; "
                "Envoy's ext_authz config will be stale.",
            )

    def test_mode_change_also_fires_configure_auth(self):
        """Authelia needs its own config regenerated (users_database,
        session cookie domain, etc.) when the mode flips on. Without
        configure-auth firing, the stored mode is active but Authelia
        itself has no users."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed_profile(Path(d), mode="basic")
            svc = self._svc(p)
            trigger = MagicMock()
            svc.update_auth_config(
                {"mode": "authelia"}, action_trigger=trigger)
            fired = [c[0][0] for c in trigger.call_args_list]
            self.assertIn(
                "configure-auth", fired,
                "auth mode edit didn't queue configure-auth; "
                "Authelia won't get its config file written.",
            )

    def test_idempotent_write_is_noop_and_fires_nothing(self):
        """Saving the current mode again should not spam the action
        queue — avoids a thundering herd of Envoy reloads on a form
        that the admin opens with the same values."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed_profile(Path(d), mode="authelia")
            svc = self._svc(p)
            trigger = MagicMock()
            result = svc.update_auth_config(
                {"mode": "authelia"}, action_trigger=trigger)
            # No change, no trigger.
            trigger.assert_not_called()
            self.assertEqual(result.get("status"), "no_changes")

    def test_unknown_mode_is_rejected(self):
        """An admin typo must not corrupt the profile. Unknown mode
        → clear error, profile unchanged."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed_profile(Path(d), mode="authelia")
            svc = self._svc(p)
            result = svc.update_auth_config({"mode": "typo-mode"})
            self.assertIn("error", result)
            profile = yaml.safe_load(p.read_text())
            self.assertEqual(profile["auth"]["provider"], "authelia",
                             "bad mode write leaked into profile")


if __name__ == "__main__":
    unittest.main()
