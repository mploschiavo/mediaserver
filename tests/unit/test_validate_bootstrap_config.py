import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "src" / "media_stack" / "cli" / "commands" / "validate_bootstrap_config_main.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_bootstrap_config", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ValidateBootstrapConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_basic_checks_use_active_client_bindings(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "transmission",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "transmission": {"url": "http://transmission:9091"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
        }
        self.assertEqual(self.mod.basic_checks(cfg), [])

    def test_basic_checks_fail_when_active_client_missing(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "transmission",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
        }
        errors = self.mod.basic_checks(cfg)
        self.assertTrue(
            any("missing active client section 'transmission'" in err for err in errors),
            errors,
        )

    def test_basic_checks_validate_adapter_hook_spec_format(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
            "adapter_hooks": {
                "download_client_adapter_classes": {"qbittorrent": "invalid-spec-without-colon"}
            },
        }
        errors = self.mod.basic_checks(cfg)
        self.assertTrue(
            any("unsupported" in err for err in errors),
            errors,
        )

    def test_basic_checks_validate_operation_handler_spec_format(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
            "adapter_hooks": {"operation_handlers": {"custom_operation": "invalid-handler-spec"}},
        }
        errors = self.mod.basic_checks(cfg)
        self.assertTrue(
            any("invalid hook spec" in err for err in errors),
            errors,
        )

    def test_basic_checks_validate_app_service_class_spec_format(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
            "adapter_hooks": {
                "app_service_classes": {"jellyseerr_service": "invalid-service-spec"}
            },
        }
        errors = self.mod.basic_checks(cfg)
        self.assertTrue(
            any("unsupported" in err for err in errors),
            errors,
        )

    def test_basic_checks_validate_technology_aliases_shape(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
            "adapter_hooks": {
                "technology_aliases": {
                    "qbit": "",
                }
            },
        }
        errors = self.mod.basic_checks(cfg)
        self.assertTrue(
            any("unsupported" in err and "technology_aliases" in err for err in errors),
            errors,
        )

    def test_basic_checks_validate_media_server_operation_plan_shape(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
            "media_server": {
                "operation_plans": {
                    "jellyfin": {
                        "prewarm_mode": {
                            "steps": [
                                {"operation": "ensure_jellyfin_prewarm"},
                            ]
                        }
                    }
                }
            },
        }
        self.assertEqual(self.mod.basic_checks(cfg), [])

    def test_basic_checks_allow_missing_optional_download_client_bindings(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "",
                "usenet_client": "",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
        }
        errors = self.mod.basic_checks(cfg)
        self.assertFalse(
            any("technology_bindings.torrent_client" in err for err in errors),
            errors,
        )
        self.assertFalse(
            any("technology_bindings.usenet_client" in err for err in errors),
            errors,
        )

    def test_basic_checks_reject_media_server_operation_step_without_operation(self):
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
            "media_server": {
                "operation_plans": {
                    "jellyfin": {
                        "prewarm_mode": {
                            "steps": [
                                {"args": ["cfg", "config_root"]},
                            ]
                        }
                    }
                }
            },
        }
        errors = self.mod.basic_checks(cfg)
        self.assertTrue(
            any(".operation: required non-empty string" in err for err in errors),
            errors,
        )

    def test_basic_checks_reject_missing_config_version(self):
        cfg = {
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {"url": "http://qbittorrent:8080"},
                "sabnzbd": {"url": "http://sabnzbd:8080"},
            },
        }
        errors = self.mod.basic_checks(cfg)
        self.assertTrue(any("config_version" in err for err in errors), errors)


if __name__ == "__main__":
    unittest.main()
