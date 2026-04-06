import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.qbittorrent import compose_preflight as MODULE  # noqa: E402


class ComposeQbittorrentPreflightTests(unittest.TestCase):
    def test_sets_default_stack_credentials_and_updates_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("TZ=America/Chicago\n", encoding="utf-8")
            docker = mock.Mock()
            docker.get_container.return_value = None
            env = {"TZ": "America/Chicago"}

            result = MODULE.ensure_compose_torrent_client_credentials(
                compose_env=env,
                compose_env_file=env_file,
                namespace="media-dev",
                docker=docker,
                info=mock.Mock(),
            )

            self.assertEqual(result["STACK_ADMIN_USERNAME"], "admin")
            self.assertEqual(result["STACK_ADMIN_PASSWORD"], "media-dev")
            payload = env_file.read_text(encoding="utf-8")
            self.assertIn("STACK_ADMIN_USERNAME=admin", payload)
            self.assertIn("STACK_ADMIN_PASSWORD=media-dev", payload)

    @mock.patch.object(MODULE, "_set_credentials_with_container")
    @mock.patch.object(MODULE, "_wait_for_login")
    def test_skips_sync_when_stack_credentials_already_work(
        self, wait_login_mock, set_credentials_mock
    ):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        env = {
            "STACK_ADMIN_USERNAME": "alice",
            "STACK_ADMIN_PASSWORD": "pw-1",
        }
        wait_login_mock.return_value = True

        result = MODULE.ensure_compose_torrent_client_credentials(
            compose_env=env,
            compose_env_file=None,
            namespace="media-dev",
            docker=docker,
            info=mock.Mock(),
        )

        self.assertEqual(result["STACK_ADMIN_USERNAME"], "alice")
        self.assertEqual(result["STACK_ADMIN_PASSWORD"], "pw-1")
        set_credentials_mock.assert_not_called()
        self.assertEqual(wait_login_mock.call_count, 1)

    @mock.patch.object(MODULE, "_reset_auth_config_in_container")
    @mock.patch.object(MODULE, "_read_temporary_password")
    @mock.patch.object(MODULE, "_set_credentials_with_container")
    @mock.patch.object(MODULE, "_wait_for_login")
    def test_syncs_via_temporary_password_when_stack_credentials_fail(
        self,
        wait_login_mock,
        set_credentials_mock,
        read_temp_mock,
        reset_auth_mock,
    ):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        env = {
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "media-dev",
        }
        wait_login_mock.side_effect = [False, True]
        read_temp_mock.return_value = "TempPass123"
        set_credentials_mock.return_value = True
        reset_auth_mock.return_value = False

        MODULE.ensure_compose_torrent_client_credentials(
            compose_env=env,
            compose_env_file=None,
            namespace="media-dev",
            docker=docker,
            info=mock.Mock(),
        )

        set_credentials_mock.assert_called_once()
        kwargs = set_credentials_mock.call_args.kwargs
        self.assertEqual(kwargs["auth_user"], "admin")
        self.assertEqual(kwargs["auth_pass"], "TempPass123")
        self.assertEqual(kwargs["target_user"], "admin")
        self.assertEqual(kwargs["target_pass"], "media-dev")
        reset_auth_mock.assert_not_called()
        self.assertEqual(wait_login_mock.call_count, 2)

    @mock.patch.object(MODULE, "_restart_container")
    @mock.patch.object(MODULE, "_reset_auth_config_in_container")
    @mock.patch.object(MODULE, "_read_temporary_password")
    @mock.patch.object(MODULE, "_set_credentials_with_container")
    @mock.patch.object(MODULE, "_wait_for_login")
    def test_resets_auth_when_temporary_password_unavailable(
        self,
        wait_login_mock,
        set_credentials_mock,
        read_temp_mock,
        reset_auth_mock,
        restart_mock,
    ):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        env = {
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "media-dev",
        }
        wait_login_mock.side_effect = [False, True]
        read_temp_mock.side_effect = ["", "TempAfterReset"]
        set_credentials_mock.return_value = True
        reset_auth_mock.return_value = True
        restart_mock.return_value = True

        MODULE.ensure_compose_torrent_client_credentials(
            compose_env=env,
            compose_env_file=None,
            namespace="media-dev",
            docker=docker,
            info=mock.Mock(),
        )

        reset_auth_mock.assert_called_once()
        restart_mock.assert_called_once()
        kwargs = set_credentials_mock.call_args.kwargs
        self.assertEqual(kwargs["auth_pass"], "TempAfterReset")
        self.assertEqual(wait_login_mock.call_count, 2)

    @mock.patch.object(MODULE, "_restart_container")
    @mock.patch.object(MODULE, "_reset_auth_config_in_container")
    @mock.patch.object(MODULE, "_read_temporary_password")
    @mock.patch.object(MODULE, "_wait_for_login")
    def test_raises_when_temporary_password_still_unavailable_after_reset(
        self, wait_login_mock, read_temp_mock, reset_auth_mock, restart_mock
    ):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        env = {
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "media-dev",
        }
        wait_login_mock.return_value = False
        read_temp_mock.side_effect = ["", ""]
        reset_auth_mock.return_value = True
        restart_mock.return_value = True

        with self.assertRaises(RuntimeError):
            MODULE.ensure_compose_torrent_client_credentials(
                compose_env=env,
                compose_env_file=None,
                namespace="media-dev",
                docker=docker,
                info=mock.Mock(),
            )


if __name__ == "__main__":
    unittest.main()
