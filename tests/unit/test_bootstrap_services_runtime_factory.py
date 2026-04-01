import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.config_models import (  # noqa: E402
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
)
from bootstrap_services.enums import BootstrapMode  # noqa: E402
from bootstrap_services.runtime_factory import (  # noqa: E402
    BootstrapCliArgs,
    BootstrapRuntimeFactoryDependencies,
    BootstrapRuntimeFactoryService,
)


class RuntimeFactoryServiceTests(unittest.TestCase):
    def _qbit_env(self):
        return mock.patch.dict(
            os.environ,
            {
                "STACK_ADMIN_USERNAME": "admin",
                "STACK_ADMIN_PASSWORD": "media-stack-admin",
            },
            clear=False,
        )

    def _factory(self, read_api_key=None):
        deps = BootstrapRuntimeFactoryDependencies(
            load_bootstrap_default_json=lambda filename, fallback: fallback,
            deep_merge_objects=lambda base, override: {**dict(base or {}), **dict(override or {})},
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            coerce_list=lambda value: (
                value if isinstance(value, list) else ([] if value is None else [value])
            ),
            env_truthy=lambda name, default=False: str(os.environ.get(name, str(default)))
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            read_api_key=read_api_key or (lambda config_root, app: f"{app}-key"),
            build_sab_remote_path_mappings=lambda sab_cfg: [
                {
                    "host": "sabnzbd",
                    "remotePath": "/config/Downloads/complete",
                    "localPath": "/data/usenet/completed",
                }
            ],
        )
        return BootstrapRuntimeFactoryService(deps=deps)

    def _args(self, mode=BootstrapMode.FULL):
        return BootstrapCliArgs(
            mode=mode,
            config_path="bootstrap/media-stack.bootstrap.json",
            config_root="/srv-config",
            wait_timeout=30,
            auto_prowlarr_indexers=False,
        )

    def test_full_mode_reads_servarr_and_prowlarr_keys(self):
        read_api_key = mock.Mock(side_effect=lambda _root, app: f"{app}-api")
        factory = self._factory(read_api_key=read_api_key)
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [
                {
                    "name": "Sonarr",
                    "implementation": "sonarr",
                    "url": "http://sonarr:8989",
                    "root_folder": "/media/tv",
                }
            ],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {
                    "configure_arr_clients": True,
                    "username_env": "STACK_ADMIN_USERNAME",
                    "password_env": "STACK_ADMIN_PASSWORD",
                },
                "sabnzbd": {"configure_arr_clients": False},
            },
        }

        with self._qbit_env():
            result = factory.build(self._args(mode=BootstrapMode.FULL), cfg)

        self.assertEqual(result.runtime.prowlarr_key, "prowlarr-api")
        self.assertEqual(result.runtime.app_keys.get("sonarr"), "sonarr-api")
        self.assertIsInstance(result.runtime.arr_media_management_cfg, ArrMediaManagementPolicy)
        self.assertIsInstance(result.runtime.arr_download_handling_cfg, ArrDownloadHandlingPolicy)
        self.assertIsInstance(result.runtime.arr_quality_upgrade_cfg, ArrQualityUpgradePolicy)
        self.assertTrue(result.plan.configure_arr_clients)
        self.assertEqual(read_api_key.call_count, 2)

    def test_non_full_mode_skips_api_key_reads(self):
        read_api_key = mock.Mock(return_value="unused")
        factory = self._factory(read_api_key=read_api_key)
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [
                {
                    "name": "Sonarr",
                    "implementation": "sonarr",
                    "url": "http://sonarr:8989",
                    "root_folder": "/media/tv",
                }
            ],
            "technology_bindings": {
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "media_server": "jellyfin",
            },
            "download_clients": {
                "qbittorrent": {
                    "configure_arr_clients": False,
                    "username_env": "STACK_ADMIN_USERNAME",
                    "password_env": "STACK_ADMIN_PASSWORD",
                },
                "sabnzbd": {"configure_arr_clients": False},
            },
        }

        with self._qbit_env():
            result = factory.build(self._args(mode=BootstrapMode.MEDIA_SERVER_PREWARM), cfg)

        self.assertEqual(result.runtime.prowlarr_key, "")
        self.assertEqual(result.runtime.app_keys, {})
        read_api_key.assert_not_called()

    def test_fully_preconfigured_sets_default_app_auth(self):
        factory = self._factory()
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
                "qbittorrent": {
                    "configure_arr_clients": False,
                    "username_env": "STACK_ADMIN_USERNAME",
                    "password_env": "STACK_ADMIN_PASSWORD",
                },
                "sabnzbd": {"configure_arr_clients": False},
            },
        }

        with mock.patch.dict(
            os.environ,
            {
                "FULLY_PRECONFIGURED": "1",
                "STACK_ADMIN_USERNAME": "admin",
                "STACK_ADMIN_PASSWORD": "media-stack-admin",
            },
            clear=False,
        ):
            result = factory.build(self._args(mode=BootstrapMode.MEDIA_HYGIENE), cfg)

        app_auth = result.runtime.app_auth_cfg
        self.assertTrue(app_auth.get("enabled"))
        self.assertEqual(app_auth.get("method"), "Forms")
        self.assertEqual(app_auth.get("include", []), ["Prowlarr"])

    def test_technology_bindings_select_active_clients_and_media_backend(self):
        factory = self._factory()
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "transmission",
                "usenet_client": "sabnzbd",
                "media_server": "emby",
            },
            "download_clients": {
                "transmission": {
                    "url": "http://transmission:9091",
                    "name": "Transmission",
                    "configure_arr_clients": True,
                },
                "sabnzbd": {
                    "url": "http://sabnzbd:8080",
                    "name": "SABnzbd",
                    "configure_arr_clients": True,
                },
            },
        }

        with self._qbit_env():
            result = factory.build(self._args(mode=BootstrapMode.FULL), cfg)

        self.assertEqual(result.runtime.torrent_client_key, "transmission")
        self.assertEqual(result.runtime.usenet_client_key, "sabnzbd")
        self.assertEqual(result.runtime.qbit_cfg.get("name"), "Transmission")
        self.assertEqual(result.runtime.media_server_backend, "emby")

    def test_technology_bindings_apply_aliases_from_plugin_manifests(self):
        factory = self._factory()
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "technology_bindings": {
                "torrent_client": "qbit",
                "usenet_client": "sab",
                "media_server": "jf",
                "request_manager": "openseer",
            },
            "download_clients": {
                "qbittorrent": {
                    "url": "http://qbittorrent:8080",
                    "name": "qBittorrent",
                    "configure_arr_clients": True,
                    "username_env": "STACK_ADMIN_USERNAME",
                    "password_env": "STACK_ADMIN_PASSWORD",
                },
                "sabnzbd": {
                    "url": "http://sabnzbd:8080",
                    "name": "SABnzbd",
                    "configure_arr_clients": True,
                },
            },
        }

        with self._qbit_env():
            result = factory.build(self._args(mode=BootstrapMode.FULL), cfg)

        self.assertEqual(result.runtime.torrent_client_key, "qbittorrent")
        self.assertEqual(result.runtime.usenet_client_key, "sabnzbd")
        self.assertEqual(result.runtime.media_server_backend, "jellyfin")
        self.assertEqual(result.runtime.request_manager_backend, "openseerr")

    def test_missing_required_bindings_raise(self):
        factory = self._factory()
        cfg = {
            "config_version": 2,
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "download_clients": {
                "transmission": {
                    "url": "http://transmission:9091",
                    "name": "Transmission",
                    "configure_arr_clients": True,
                },
                "sabnzbd": {
                    "url": "http://sabnzbd:8080",
                    "name": "SABnzbd",
                    "configure_arr_clients": True,
                },
            },
        }

        with self.assertRaises(ValueError):
            factory.build(self._args(mode=BootstrapMode.FULL), cfg)

    def test_adapter_registration_overrides_are_rejected(self):
        factory = self._factory()
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
                "qbittorrent": {
                    "configure_arr_clients": False,
                    "username_env": "STACK_ADMIN_USERNAME",
                    "password_env": "STACK_ADMIN_PASSWORD",
                },
                "sabnzbd": {"configure_arr_clients": False},
            },
            "adapter_hooks": {
                "download_client_adapter_classes": {
                    "qbittorrent": "bootstrap_services.download_client_adapters.qbittorrent:QbittorrentDownloadClientAdapter"
                }
            },
        }
        with self._qbit_env():
            with self.assertRaises(ValueError):
                factory.build(self._args(mode=BootstrapMode.FULL), cfg)

    def test_load_config_merges_base_and_env_overlay_when_enabled(self):
        factory = self._factory()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "scripts").mkdir(parents=True)
            (root / "bootstrap").mkdir(parents=True)
            (root / "config" / "runtime" / "overlays").mkdir(parents=True)

            (root / "config" / "runtime" / "base.json").write_text(
                '{"trigger_indexer_sync": false, "prowlarr_url": "http://base:9696"}\n',
                encoding="utf-8",
            )
            (root / "config" / "runtime" / "overlays" / "dev.json").write_text(
                '{"trigger_indexer_sync": true, "download_clients": {"qbittorrent": {"name": "qB"}}}\n',
                encoding="utf-8",
            )
            config_path = root / "bootstrap" / "config.json"
            config_path.write_text(
                (
                    "{"
                    '"config_version":2,'
                    '"config_overlays":{"enabled":true,"env":"prod",'
                    '"base_path":"config/runtime/base.json","overlay_dir":"config/runtime/overlays"},'
                    '"technology_bindings":{"torrent_client":"qbittorrent","usenet_client":"sabnzbd","media_server":"jellyfin"},'
                    '"arr_apps":[],"prowlarr_url":"http://override:9696"'
                    "}\n"
                ),
                encoding="utf-8",
            )

            merged = factory.load_config(str(config_path), runtime_env="dev")
            self.assertTrue(bool(merged.get("trigger_indexer_sync")))
            self.assertEqual(str(merged.get("prowlarr_url")), "http://override:9696")
            self.assertEqual(
                str(
                    (
                        ((merged.get("download_clients") or {}).get("qbittorrent") or {}).get(
                            "name"
                        )
                        or ""
                    )
                ),
                "qB",
            )

    def test_maintainerr_integrations_default_to_enabled_when_maintainerr_enabled(self):
        factory = self._factory()
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
                "qbittorrent": {
                    "configure_arr_clients": False,
                    "username_env": "STACK_ADMIN_USERNAME",
                    "password_env": "STACK_ADMIN_PASSWORD",
                },
                "sabnzbd": {"configure_arr_clients": False},
            },
            "maintainerr": {
                "enabled": True,
                "required": False,
            },
        }

        with self._qbit_env():
            result = factory.build(self._args(mode=BootstrapMode.FULL), cfg)

        self.assertTrue(result.runtime.configure_maintainerr_policy)
        self.assertTrue(result.runtime.configure_maintainerr_integrations)
        self.assertFalse(result.runtime.maintainerr_integrations_required)


if __name__ == "__main__":
    unittest.main()
