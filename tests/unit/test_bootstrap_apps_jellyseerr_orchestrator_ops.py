import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyseerr import orchestrator_ops as module  # noqa: E402


class _StubSvc:
    def __init__(self):
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(str(msg))

    @staticmethod
    def bool_cfg(cfg, key, default=False):
        return bool((cfg or {}).get(key, default))

    @staticmethod
    def normalize_url(url: str) -> str:
        return str(url or "").rstrip("/")

    @staticmethod
    def normalize_base_path(path: str) -> str:
        token = str(path or "").strip()
        if not token:
            return ""
        if not token.startswith("/"):
            token = "/" + token
        return token.rstrip("/")

    @staticmethod
    def wait_for_service(_name: str, _base_url: str, _path: str, _timeout: int) -> None:
        return None

    @staticmethod
    def read_jellyseerr_api_key(_config_root: str, _timeout: int) -> str:
        return "jellyseerr-key"

    @staticmethod
    def get_arr_app(_arr_apps, _name: str):
        return None


class JellyseerrOrchestratorOpsTests(unittest.TestCase):
    def test_configure_calls_local_admin_seed_on_happy_path(self):
        svc = _StubSvc()
        cfg = {"jellyseerr": {"enabled": True, "enforce_settings_file": False}}

        with (
            mock.patch.object(module, "ensure_main_settings"),
            mock.patch.object(module, "ensure_jellyfin_settings"),
            mock.patch.object(module, "configure_via_settings_file") as file_bootstrap,
            mock.patch.object(module, "ensure_local_admin_user") as seed_admin,
        ):
            module.configure(
                svc=svc,
                cfg=cfg,
                arr_apps=[],
                app_keys={},
                config_root="/srv-config",
                wait_timeout=30,
            )

        file_bootstrap.assert_not_called()
        seed_admin.assert_called_once_with(svc, cfg, "/srv-config")

    def test_configure_calls_local_admin_seed_after_permission_fallback(self):
        svc = _StubSvc()
        cfg = {"jellyseerr": {"enabled": True}}

        with (
            mock.patch.object(
                module,
                "ensure_main_settings",
                side_effect=RuntimeError("(HTTP 403) permission denied"),
            ),
            mock.patch.object(module, "ensure_jellyfin_settings"),
            mock.patch.object(module, "configure_via_settings_file") as file_bootstrap,
            mock.patch.object(module, "ensure_local_admin_user") as seed_admin,
        ):
            module.configure(
                svc=svc,
                cfg=cfg,
                arr_apps=[],
                app_keys={},
                config_root="/srv-config",
                wait_timeout=30,
            )

        file_bootstrap.assert_called_once_with(svc, cfg, [], {}, "/srv-config")
        seed_admin.assert_called_once_with(svc, cfg, "/srv-config")

    def test_configure_seeds_local_admin_before_non_permission_failure(self):
        svc = _StubSvc()
        cfg = {"jellyseerr": {"enabled": True}}

        with (
            mock.patch.object(module, "ensure_main_settings", side_effect=RuntimeError("boom")),
            mock.patch.object(module, "ensure_jellyfin_settings"),
            mock.patch.object(module, "configure_via_settings_file") as file_bootstrap,
            mock.patch.object(module, "ensure_local_admin_user") as seed_admin,
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                module.configure(
                    svc=svc,
                    cfg=cfg,
                    arr_apps=[],
                    app_keys={},
                    config_root="/srv-config",
                    wait_timeout=30,
                )

        file_bootstrap.assert_not_called()
        seed_admin.assert_called_once_with(svc, cfg, "/srv-config")


if __name__ == "__main__":
    unittest.main()
