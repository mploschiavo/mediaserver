import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

# services.apps.jellyfin.compose_preflight is a star-shim that drops
# the underscore-prefixed module aliases (_http_request, etc.) — the
# canonical home is infrastructure.jellyfin.compose_preflight where
# those aliases are defined.
from media_stack.infrastructure.jellyfin import compose_preflight as MODULE  # noqa: E402


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
    @mock.patch.object(MODULE, "_http_request")
    def test_reuses_existing_valid_api_key(
        self, http_request, auth_cls, lookup_user_id, validate_api_key
    ):
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
        http_request.return_value = (200, {"StartupWizardCompleted": True}, "{}")
        validate_api_key.return_value = True
        lookup_user_id.return_value = "user-1"

        out = MODULE.ensure_compose_jellyfin_bootstrap_access(
            compose_env=env,
            namespace="media-dev",
            docker=docker,
            info=info,
        )

        auth_instance = auth_cls.return_value
        auth_instance.startup_wizard_if_needed.assert_called_once_with(
            "http://jellyfin:8096",
            "admin",
            "secret",
        )
        validate_api_key.assert_called_once_with(
            "http://jellyfin:8096",
            "existing-key",
            http_request=mock.ANY,
        )
        self.assertEqual(out["JELLYFIN_API_KEY"], "existing-key")
        self.assertEqual(out["JELLYFIN_USER_ID"], "user-1")

    @mock.patch.object(MODULE, "ensure_api_key")
    @mock.patch.object(MODULE, "validate_api_key")
    @mock.patch.object(MODULE, "JellyfinBootstrapAuthService")
    @mock.patch.object(MODULE, "_http_request")
    def test_authenticates_and_generates_api_key_when_missing(
        self,
        http_request,
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
        http_request.return_value = (200, {"StartupWizardCompleted": False}, "{}")
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

        auth_instance.startup_wizard_if_needed.assert_called_once_with(
            "http://jellyfin:8096",
            "admin",
            "secret",
        )
        self.assertEqual(out["JELLYFIN_API_KEY"], "generated-key")
        self.assertEqual(out["JELLYFIN_USER_ID"], "user-1")

    @mock.patch.object(MODULE, "ensure_api_key")
    @mock.patch.object(MODULE, "validate_api_key")
    @mock.patch.object(MODULE, "JellyfinBootstrapAuthService")
    @mock.patch.object(MODULE, "_http_request")
    def test_uses_explicit_service_host_and_port_when_provided(
        self,
        http_request,
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
            "JELLYFIN_SERVICE_HOST": "jellyfin-alt",
            "JELLYFIN_SERVICE_PORT": "18096",
        }
        http_request.return_value = (200, {"StartupWizardCompleted": True}, "{}")
        auth_instance = auth_cls.return_value
        auth_instance.try_authenticate_jellyfin.return_value = ("session-token", "user-1")
        ensure_api_key.return_value = "generated-key"
        validate_api_key.return_value = True

        MODULE.ensure_compose_jellyfin_bootstrap_access(
            compose_env=env,
            namespace="media-dev",
            docker=docker,
            info=info,
        )

        auth_instance.startup_wizard_if_needed.assert_called_once_with(
            "http://jellyfin-alt:18096",
            "admin",
            "secret",
        )

    @mock.patch.object(MODULE, "ensure_api_key")
    @mock.patch.object(MODULE, "validate_api_key")
    @mock.patch.object(MODULE, "JellyfinBootstrapAuthService")
    @mock.patch.object(MODULE, "_http_request")
    def test_falls_back_to_container_ip_when_service_endpoint_unreachable(
        self,
        http_request,
        auth_cls,
        validate_api_key,
        ensure_api_key,
    ):
        container = mock.Mock()
        container.attrs = {
            "NetworkSettings": {
                "Networks": {
                    "media-dev_default": {
                        "IPAddress": "172.19.0.4",
                    }
                }
            }
        }
        docker = mock.Mock()
        docker.get_container.return_value = container
        info = mock.Mock()
        env = {
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "secret",
        }

        def _request_side_effect(base_url, path, *, host_header, **_kwargs):
            if path == "/System/Info/Public" and base_url == "http://jellyfin:8096":
                return 0, None, "unreachable"
            if path == "/System/Info/Public" and base_url == "http://172.19.0.4:8096":
                return 200, {"StartupWizardCompleted": False}, "{}"
            return 200, {}, "{}"

        http_request.side_effect = _request_side_effect
        auth_instance = auth_cls.return_value
        auth_instance.try_authenticate_jellyfin.return_value = ("session-token", "user-1")
        ensure_api_key.return_value = "generated-key"
        validate_api_key.return_value = True

        MODULE.ensure_compose_jellyfin_bootstrap_access(
            compose_env=env,
            namespace="media-dev",
            docker=docker,
            info=info,
        )

        auth_instance.startup_wizard_if_needed.assert_called_once_with(
            "http://172.19.0.4:8096",
            "admin",
            "secret",
        )
        info.assert_any_call(
            "Compose Jellyfin preflight: fallback to container-network endpoint "
            "http://172.19.0.4:8096 after bootstrap endpoint http://jellyfin:8096 was unreachable."
        )


if __name__ == "__main__":
    unittest.main()
