import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.jellyfin import compose_preflight as MODULE  # noqa: E402


class ComposeJellyfinPreflightTests(unittest.TestCase):
    def test_skips_when_jellyfin_container_missing(self):
        docker = mock.Mock()
        docker.get_container.return_value = None
        info = mock.Mock()

        out = MODULE.ensure_compose_jellyfin_bootstrap_access(
            compose_env={},
            namespace="media-dev",
            docker=docker,
            info=info,
        )

        self.assertEqual(out, {})
        info.assert_called_once()

    @mock.patch.object(MODULE, "validate_api_key")
    @mock.patch.object(MODULE, "lookup_user_id_with_api_key")
    @mock.patch.object(MODULE, "JellyfinBootstrapAuthService")
    def test_reuses_existing_valid_api_key(self, auth_cls, lookup_user_id, validate_api_key):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        info = mock.Mock()
        env = {
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "secret",
            "JELLYFIN_HOST": "jellyfin.media-dev.local",
            "TRAEFIK_HTTP_PORT": "18080",
            "JELLYFIN_API_KEY": "existing-key",
        }
        validate_api_key.return_value = True
        lookup_user_id.return_value = "user-1"

        out = MODULE.ensure_compose_jellyfin_bootstrap_access(
            compose_env=env,
            namespace="media-dev",
            docker=docker,
            info=info,
        )

        auth_instance = auth_cls.return_value
        auth_instance.startup_wizard_if_needed.assert_called_once()
        self.assertEqual(out["JELLYFIN_API_KEY"], "existing-key")
        self.assertEqual(out["JELLYFIN_USER_ID"], "user-1")

    @mock.patch.object(MODULE, "ensure_api_key")
    @mock.patch.object(MODULE, "validate_api_key")
    @mock.patch.object(MODULE, "JellyfinBootstrapAuthService")
    def test_authenticates_and_generates_api_key_when_missing(
        self,
        auth_cls,
        validate_api_key,
        ensure_api_key,
    ):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        info = mock.Mock()
        env = {
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "secret",
            "JELLYFIN_HOST": "jellyfin.media-dev.local",
            "TRAEFIK_HTTP_PORT": "18080",
        }
        auth_instance = auth_cls.return_value
        auth_instance.try_authenticate_jellyfin.return_value = ("session-token", "user-1")
        ensure_api_key.return_value = "generated-key"
        validate_api_key.return_value = True

        out = MODULE.ensure_compose_jellyfin_bootstrap_access(
            compose_env=env,
            namespace="media-dev",
            docker=docker,
            info=info,
        )

        self.assertEqual(out["JELLYFIN_API_KEY"], "generated-key")
        self.assertEqual(out["JELLYFIN_USER_ID"], "user-1")


if __name__ == "__main__":
    unittest.main()
