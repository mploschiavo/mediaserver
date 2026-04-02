import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.stack import compose_preflight as MODULE  # noqa: E402


class ComposeStackPreflightTests(unittest.TestCase):
    def test_creates_required_media_and_data_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_root = root / "media-root"
            data_root = root / "data-root"
            config_root = root / "config-root"
            env = {
                "MEDIA_ROOT": str(media_root),
                "DATA_ROOT": str(data_root),
                "CONFIG_ROOT": str(config_root),
            }
            info = mock.Mock()

            MODULE.ensure_compose_stack_filesystem_paths(
                compose_env=env,
                info=info,
            )

            self.assertTrue((media_root / "media" / "tv").exists())
            self.assertTrue((media_root / "media" / "movies").exists())
            self.assertTrue((data_root / "torrents" / "completed" / "tv").exists())
            self.assertTrue((data_root / "usenet" / "completed" / "movies").exists())
            self.assertTrue((config_root / "maintainerr").exists())
            info.assert_called_once()

    def test_skips_when_roots_missing(self):
        info = mock.Mock()
        out = MODULE.ensure_compose_stack_filesystem_paths(
            compose_env={},
            info=info,
        )

        self.assertEqual(out, {})
        info.assert_called_once()

    def test_reconciles_config_path_owner_when_uid_gid_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "MEDIA_ROOT": str(root / "media-root"),
                "DATA_ROOT": str(root / "data-root"),
                "CONFIG_ROOT": str(root / "config-root"),
                "PUID": "12345",
                "PGID": "12345",
            }
            info = mock.Mock()
            docker = mock.Mock()

            with mock.patch.object(
                MODULE,
                "_reconcile_permissions_with_helper",
                return_value=True,
            ) as reconcile:
                MODULE.ensure_compose_stack_filesystem_paths(
                    compose_env=env,
                    docker=docker,
                    info=info,
                )

            reconcile.assert_called_once()
            kwargs = reconcile.call_args.kwargs
            self.assertEqual(kwargs["uid"], 12345)
            self.assertEqual(kwargs["gid"], 12345)
            self.assertEqual(kwargs["docker"], docker)
            self.assertTrue(str(kwargs["target_path"]).endswith("config-root/maintainerr"))


if __name__ == "__main__":
    unittest.main()
