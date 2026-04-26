import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.sabnzbd import compose_preflight as MODULE  # noqa: E402


class ComposeSabnzbdPreflightTests(unittest.TestCase):
    def test_skips_when_container_missing(self):
        docker = mock.Mock()
        docker.get_container.return_value = None
        info = mock.Mock()

        out = MODULE.ensure_compose_sabnzbd_api_access(
            compose_env={},
            namespace="media-dev",
            docker=docker,
            info=info,
        )

        self.assertEqual(out, {})
        info.assert_called()

    def test_restarts_after_reconcile_change(self):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        info = mock.Mock()

        with (
            mock.patch.object(MODULE, "_reconcile_sabnzbd_config", return_value=(True, "")),
            mock.patch.object(MODULE, "_restart_container", return_value=True) as restart_mock,
            mock.patch.object(MODULE, "_wait_for_ready", return_value=True) as wait_mock,
        ):
            MODULE.ensure_compose_sabnzbd_api_access(
                compose_env={"SABNZBD_HOST": "sabnzbd.local"},
                namespace="media-dev",
                docker=docker,
                info=info,
            )

        restart_mock.assert_called_once()
        wait_mock.assert_called_once()
        info.assert_called()

    def test_no_restart_when_no_change(self):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()
        info = mock.Mock()

        with (
            mock.patch.object(MODULE, "_reconcile_sabnzbd_config", return_value=(False, "")),
            mock.patch.object(MODULE, "_restart_container") as restart_mock,
            mock.patch.object(MODULE, "_wait_for_ready") as wait_mock,
        ):
            MODULE.ensure_compose_sabnzbd_api_access(
                compose_env={"SABNZBD_HOST": "sabnzbd.local"},
                namespace="media-dev",
                docker=docker,
                info=info,
            )

        restart_mock.assert_not_called()
        wait_mock.assert_not_called()
        info.assert_called()

    def test_raises_when_restart_fails(self):
        docker = mock.Mock()
        docker.get_container.return_value = mock.Mock()

        with (
            mock.patch.object(MODULE, "_reconcile_sabnzbd_config", return_value=(True, "")),
            mock.patch.object(MODULE, "_restart_container", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                MODULE.ensure_compose_sabnzbd_api_access(
                    compose_env={},
                    namespace="media-dev",
                    docker=docker,
                    info=mock.Mock(),
                )

    def test_desired_host_whitelist_includes_namespace_and_gateway(self):
        whitelist = MODULE._desired_host_whitelist(
            {
                "SABNZBD_HOST": "sabnzbd.local",
                "APP_GATEWAY_HOST": "apps.media-dev.local",
            },
            "media-dev",
        )
        self.assertIn("sabnzbd", whitelist)
        self.assertIn("sabnzbd.media-dev", whitelist)
        self.assertIn("sabnzbd.media-dev.svc", whitelist)
        self.assertIn("sabnzbd.media-dev.svc.cluster.local", whitelist)
        self.assertIn("sabnzbd.local", whitelist)
        self.assertIn("apps.media-dev.local", whitelist)


if __name__ == "__main__":
    unittest.main()
