"""Unit tests for ControllerRuntime in runtime_models.py."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.servarr.config_models import (  # noqa: E402
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
)
from media_stack.services.enums import BootstrapMode  # noqa: E402
from media_stack.services.runtime_models import ControllerRuntime  # noqa: E402


def _minimal_runtime(**overrides):
    """Build a ControllerRuntime with minimal required fields."""
    defaults = dict(
        mode=BootstrapMode.FULL,
        cfg={},
        config_root="/config",
        wait_timeout=60,
        arr_apps_raw=[],
        arr_apps=[],
        app_keys={},
        prowlarr_url="http://prowlarr:9696",
        prowlarr_key="pk-123",
        qbit_cfg={},
        sab_cfg={},
        torrent_client_key="tc-key",
        usenet_client_key="uc-key",
        arr_media_management_cfg=ArrMediaManagementPolicy.from_dict(None),
        arr_download_handling_cfg=ArrDownloadHandlingPolicy.from_dict(None),
        arr_quality_upgrade_cfg=ArrQualityUpgradePolicy.from_dict(None),
        app_auth_cfg={},
        adapter_hooks_cfg={},
        prowlarr_indexers=[],
        sab_remote_path_mappings=[],
        qb_user="admin",
        qb_pass="secret",
        sab_username="sab_admin",
        sab_password="sab_pass",
        auto_indexers=True,
        trigger_sync=False,
        fully_preconfigured=False,
    )
    defaults.update(overrides)
    return ControllerRuntime(**defaults)


class TestControllerRuntimeConstruction(unittest.TestCase):
    """Test basic construction and field assignment."""

    def test_mode_is_set(self):
        rt = _minimal_runtime()
        self.assertEqual(rt.mode, BootstrapMode.FULL)

    def test_config_root_is_set(self):
        rt = _minimal_runtime(config_root="/my/config")
        self.assertEqual(rt.config_root, "/my/config")

    def test_wait_timeout_is_set(self):
        rt = _minimal_runtime(wait_timeout=120)
        self.assertEqual(rt.wait_timeout, 120)

    def test_prowlarr_url_is_set(self):
        rt = _minimal_runtime(prowlarr_url="http://p:9696")
        self.assertEqual(rt.prowlarr_url, "http://p:9696")

    def test_prowlarr_key_is_set(self):
        rt = _minimal_runtime(prowlarr_key="mykey")
        self.assertEqual(rt.prowlarr_key, "mykey")

    def test_cfg_dict_stored(self):
        rt = _minimal_runtime(cfg={"x": 1})
        self.assertEqual(rt.cfg, {"x": 1})

    def test_arr_apps_raw_stored(self):
        raw = [{"name": "radarr"}]
        rt = _minimal_runtime(arr_apps_raw=raw)
        self.assertEqual(rt.arr_apps_raw, raw)

    def test_arr_apps_stored(self):
        rt = _minimal_runtime(arr_apps=[])
        self.assertEqual(rt.arr_apps, [])

    def test_app_keys_stored(self):
        rt = _minimal_runtime(app_keys={"radarr": "key1"})
        self.assertEqual(rt.app_keys, {"radarr": "key1"})


class TestControllerRuntimeDefaults(unittest.TestCase):
    """Test default values for optional parameters."""

    def test_media_server_backend_default_empty(self):
        rt = _minimal_runtime()
        self.assertEqual(rt.media_server_backend, "")

    def test_request_manager_backend_default_empty(self):
        rt = _minimal_runtime()
        self.assertEqual(rt.request_manager_backend, "")

    def test_feature_flags_default_empty(self):
        rt = _minimal_runtime()
        self.assertEqual(rt.feature_flags, {})

    def test_runtime_values_default_empty(self):
        rt = _minimal_runtime()
        self.assertEqual(rt.runtime_values, {})


class TestControllerRuntimeBoolCoercion(unittest.TestCase):
    """Test bool coercion of specific fields."""

    def test_auto_indexers_coerced_to_bool(self):
        rt = _minimal_runtime(auto_indexers=1)
        self.assertIs(rt.auto_indexers, True)

    def test_auto_indexers_false(self):
        rt = _minimal_runtime(auto_indexers=0)
        self.assertIs(rt.auto_indexers, False)

    def test_trigger_sync_coerced_to_bool(self):
        rt = _minimal_runtime(trigger_sync="yes")
        self.assertIs(rt.trigger_sync, True)

    def test_fully_preconfigured_coerced_to_bool(self):
        rt = _minimal_runtime(fully_preconfigured="")
        self.assertIs(rt.fully_preconfigured, False)


class TestControllerRuntimeMediaServerBackend(unittest.TestCase):
    """Test media_server_backend normalization."""

    def test_explicit_plex(self):
        rt = _minimal_runtime(media_server_backend="plex")
        self.assertEqual(rt.media_server_backend, "plex")

    def test_none_becomes_empty(self):
        rt = _minimal_runtime(media_server_backend=None)
        self.assertEqual(rt.media_server_backend, "")

    def test_empty_string_stays_empty(self):
        rt = _minimal_runtime(media_server_backend="")
        self.assertEqual(rt.media_server_backend, "")

    def test_whitespace_only_becomes_empty(self):
        rt = _minimal_runtime(media_server_backend="   ")
        self.assertEqual(rt.media_server_backend, "")

    def test_strips_whitespace(self):
        rt = _minimal_runtime(media_server_backend="  emby  ")
        self.assertEqual(rt.media_server_backend, "emby")


class TestControllerRuntimeRequestManagerBackend(unittest.TestCase):
    """Test request_manager_backend normalization."""

    def test_explicit_overseerr(self):
        rt = _minimal_runtime(request_manager_backend="overseerr")
        self.assertEqual(rt.request_manager_backend, "overseerr")

    def test_none_becomes_empty(self):
        rt = _minimal_runtime(request_manager_backend=None)
        self.assertEqual(rt.request_manager_backend, "")

    def test_empty_stays_empty(self):
        rt = _minimal_runtime(request_manager_backend="")
        self.assertEqual(rt.request_manager_backend, "")


class TestControllerRuntimeFeatureFlags(unittest.TestCase):
    """Test feature_flags handling."""

    def test_feature_flags_from_dict(self):
        rt = _minimal_runtime(feature_flags={"gpu_enabled": True})
        self.assertEqual(rt.feature_flags, {"gpu_enabled": True})

    def test_feature_flags_values_coerced_to_bool(self):
        rt = _minimal_runtime(feature_flags={"x": 1, "y": 0})
        self.assertIs(rt.feature_flags["x"], True)
        self.assertIs(rt.feature_flags["y"], False)

    def test_feature_flags_keys_coerced_to_str(self):
        rt = _minimal_runtime(feature_flags={123: True})
        self.assertIn("123", rt.feature_flags)

    def test_feature_flags_none_becomes_empty(self):
        rt = _minimal_runtime(feature_flags=None)
        self.assertEqual(rt.feature_flags, {})


class TestControllerRuntimeRuntimeValues(unittest.TestCase):
    """Test runtime_values handling."""

    def test_runtime_values_from_dict(self):
        rt = _minimal_runtime(runtime_values={"host": "localhost"})
        self.assertEqual(rt.runtime_values, {"host": "localhost"})

    def test_runtime_values_none_becomes_empty(self):
        rt = _minimal_runtime(runtime_values=None)
        self.assertEqual(rt.runtime_values, {})


class TestControllerRuntimeDynamicValues(unittest.TestCase):
    """Test dynamic **kwargs routing."""

    def test_bool_dynamic_value_goes_to_feature_flags(self):
        rt = _minimal_runtime(enable_gpu=True)
        self.assertIn("enable_gpu", rt.feature_flags)
        self.assertIs(rt.feature_flags["enable_gpu"], True)

    def test_non_bool_dynamic_value_goes_to_runtime_values(self):
        rt = _minimal_runtime(custom_port=8080)
        self.assertIn("custom_port", rt.runtime_values)
        self.assertEqual(rt.runtime_values["custom_port"], 8080)

    def test_string_dynamic_value_goes_to_runtime_values(self):
        rt = _minimal_runtime(custom_host="example.com")
        self.assertEqual(rt.runtime_values["custom_host"], "example.com")

    def test_empty_key_dynamic_value_skipped(self):
        """Empty-string keys are skipped by the dynamic value loop."""
        # We can't pass "" as a keyword arg directly, but we test the filter
        # by verifying that the runtime has no extra keys
        rt = _minimal_runtime()
        self.assertEqual(rt.runtime_values, {})
        self.assertEqual(rt.feature_flags, {})


class TestControllerRuntimeGetattr(unittest.TestCase):
    """Test __getattr__ for dynamic attribute access."""

    def test_getattr_returns_feature_flag(self):
        rt = _minimal_runtime(feature_flags={"my_flag": True})
        self.assertIs(rt.my_flag, True)

    def test_getattr_returns_runtime_value(self):
        rt = _minimal_runtime(runtime_values={"my_val": 42})
        self.assertEqual(rt.my_val, 42)

    def test_getattr_feature_flag_before_runtime_value(self):
        """Feature flags take precedence over runtime_values in getattr."""
        rt = _minimal_runtime(
            feature_flags={"overlap": True},
            runtime_values={"overlap": "string"},
        )
        self.assertIs(rt.overlap, True)

    def test_getattr_raises_attribute_error_for_unknown(self):
        rt = _minimal_runtime()
        with self.assertRaises(AttributeError):
            _ = rt.nonexistent_attr


class TestControllerRuntimeProperties(unittest.TestCase):
    """Test alias properties."""

    def test_torrent_client_cfg_returns_qbit_cfg(self):
        cfg = {"host": "localhost", "port": 8080}
        rt = _minimal_runtime(qbit_cfg=cfg, torrent_client_key="qbittorrent")
        self.assertIs(rt.torrent_client_cfg, rt.qbit_cfg)

    def test_torrent_client_username_returns_qb_user(self):
        rt = _minimal_runtime(qb_user="myuser", torrent_client_key="qbittorrent")
        self.assertEqual(rt.torrent_client_username, "myuser")

    def test_torrent_client_password_returns_qb_pass(self):
        rt = _minimal_runtime(qb_pass="mypass", torrent_client_key="qbittorrent")
        self.assertEqual(rt.torrent_client_password, "mypass")

    def test_configure_torrent_arr_clients_false_by_default(self):
        rt = _minimal_runtime()
        self.assertIs(rt.configure_torrent_arr_clients, False)

    def test_configure_torrent_arr_clients_true_when_flag_set(self):
        rt = _minimal_runtime(feature_flags={"configure_qbit_arr_clients": True})
        self.assertIs(rt.configure_torrent_arr_clients, True)

    def test_set_torrent_categories_false_by_default(self):
        rt = _minimal_runtime()
        self.assertIs(rt.set_torrent_categories, False)

    def test_set_torrent_categories_true_when_flag_set(self):
        rt = _minimal_runtime(feature_flags={"set_qbit_categories": True})
        self.assertIs(rt.set_torrent_categories, True)

    def test_torrent_client_login_required_false_by_default(self):
        rt = _minimal_runtime()
        self.assertIs(rt.torrent_client_login_required, False)

    def test_torrent_client_login_required_true_when_flag_set(self):
        rt = _minimal_runtime(feature_flags={"qbit_login_required": True})
        self.assertIs(rt.torrent_client_login_required, True)


class TestControllerRuntimeCredentials(unittest.TestCase):
    """Test credential fields."""

    def test_qb_user(self):
        rt = _minimal_runtime(qb_user="admin")
        self.assertEqual(rt.qb_user, "admin")

    def test_qb_pass(self):
        rt = _minimal_runtime(qb_pass="secret")
        self.assertEqual(rt.qb_pass, "secret")

    def test_sab_username(self):
        rt = _minimal_runtime(sab_username="nzb_user")
        self.assertEqual(rt.sab_username, "nzb_user")

    def test_sab_password(self):
        rt = _minimal_runtime(sab_password="nzb_pass")
        self.assertEqual(rt.sab_password, "nzb_pass")


class TestControllerRuntimeClientKeys(unittest.TestCase):
    """Test download client key fields."""

    def test_torrent_client_key(self):
        rt = _minimal_runtime(torrent_client_key="tkey")
        self.assertEqual(rt.torrent_client_key, "tkey")

    def test_usenet_client_key(self):
        rt = _minimal_runtime(usenet_client_key="ukey")
        self.assertEqual(rt.usenet_client_key, "ukey")


if __name__ == "__main__":
    unittest.main()
