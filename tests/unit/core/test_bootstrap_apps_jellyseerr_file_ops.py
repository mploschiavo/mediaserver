import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyseerr import file_ops


class _StubSvc:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, message: str) -> None:
        self.logs.append(str(message))

    @staticmethod
    def bool_cfg(cfg, key, default=False):
        return bool((cfg or {}).get(key, default))

    @staticmethod
    def read_json_file(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def get_arr_app(_arr_apps, _name):
        return None


class JellyseerrFileOpsTests(unittest.TestCase):
    def _write_settings(self, root: Path, payload: dict) -> None:
        settings_path = root / "jellyseerr" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(payload), encoding="utf-8")

    def _read_settings(self, root: Path) -> dict:
        return json.loads((root / "jellyseerr" / "settings.json").read_text(encoding="utf-8"))

    def test_settings_file_bootstrap_defaults_to_local_login_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_settings(
                root,
                {
                    "main": {
                        "localLogin": False,
                        "mediaServerLogin": True,
                        "newPlexLogin": True,
                    },
                    "public": {},
                },
            )
            svc = _StubSvc()
            cfg = {"jellyseerr": {"enabled": True, "set_media_server_type_jellyfin": True}}

            file_ops.configure_via_settings_file(
                svc=svc,
                cfg=cfg,
                arr_apps=[],
                app_keys={},
                config_root=str(root),
            )

            settings = self._read_settings(root)
            main_cfg = settings.get("main") or {}
            self.assertTrue(bool(main_cfg.get("localLogin")))
            self.assertFalse(bool(main_cfg.get("mediaServerLogin")))
            self.assertFalse(bool(main_cfg.get("newPlexLogin")))
            self.assertTrue(bool((settings.get("public") or {}).get("initialized")))

    def test_settings_file_bootstrap_can_enable_media_server_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_settings(
                root,
                {
                    "main": {
                        "localLogin": False,
                        "mediaServerLogin": False,
                        "newPlexLogin": False,
                    },
                    "public": {},
                },
            )
            svc = _StubSvc()
            cfg = {
                "jellyseerr": {
                    "enabled": True,
                    "set_media_server_type_jellyfin": True,
                    "enable_media_server_login": True,
                }
            }

            file_ops.configure_via_settings_file(
                svc=svc,
                cfg=cfg,
                arr_apps=[],
                app_keys={},
                config_root=str(root),
            )

            settings = self._read_settings(root)
            main_cfg = settings.get("main") or {}
            self.assertTrue(bool(main_cfg.get("localLogin")))
            self.assertTrue(bool(main_cfg.get("mediaServerLogin")))


if __name__ == "__main__":
    unittest.main()
