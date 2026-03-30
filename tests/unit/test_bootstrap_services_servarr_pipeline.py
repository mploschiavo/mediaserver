import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.servarr_adapters import (  # noqa: E402
    AdapterDependencies,
    ReadarrAdapter,
    ServarrAdapter,
    adapter_for_implementation,
)
from bootstrap_services.servarr_pipeline_service import (  # noqa: E402
    ServarrPipelineService,
    ServarrRunConfig,
)


class ServarrAdapterTests(unittest.TestCase):
    def test_adapter_factory_returns_known_and_default(self):
        self.assertEqual(type(adapter_for_implementation("sonarr")).__name__, "SonarrAdapter")
        self.assertEqual(type(adapter_for_implementation("readarr")).__name__, "ReadarrAdapter")

        unknown = adapter_for_implementation("myarr")
        self.assertIsInstance(unknown, ServarrAdapter)
        self.assertEqual(unknown.implementation, "myarr")

    def test_readarr_adapter_warns_when_metadata_optional(self):
        logs = []
        deps = AdapterDependencies(
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            log=logs.append,
            ensure_readarr_metadata_source=mock.Mock(side_effect=RuntimeError("boom")),
        )
        adapter = ReadarrAdapter()

        adapter.before_common_steps(
            deps,
            cfg={"readarr": {"metadata_source_required": False}},
            app_cfg={"implementation": "Readarr"},
            app_url="http://readarr:8787",
            api_base="/api/v1",
            api_key="abc",
        )

        self.assertTrue(any("Readarr metadata source: bootstrap skipped" in line for line in logs))

    def test_readarr_adapter_raises_when_metadata_required(self):
        deps = AdapterDependencies(
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            log=mock.Mock(),
            ensure_readarr_metadata_source=mock.Mock(side_effect=RuntimeError("boom")),
        )
        adapter = ReadarrAdapter()

        with self.assertRaises(RuntimeError):
            adapter.before_common_steps(
                deps,
                cfg={"readarr": {"metadata_source_required": True}},
                app_cfg={"implementation": "Readarr"},
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="abc",
            )


class ServarrPipelineServiceTests(unittest.TestCase):
    def _service(self):
        self.log = mock.Mock()
        self.normalize_url = mock.Mock(side_effect=lambda value: value.rstrip("/"))
        self.detect_arr_api_base = mock.Mock(side_effect=["/api/v3", "/api/v1"])
        self.ensure_app_auth_settings = mock.Mock()
        self.ensure_arr_media_management = mock.Mock()
        self.ensure_root_folder = mock.Mock()
        self.ensure_arr_download_handling = mock.Mock()
        self.ensure_arr_quality_upgrade_policy = mock.Mock()
        self.ensure_prowlarr_application = mock.Mock()
        self.ensure_arr_download_client = mock.Mock()
        self.ensure_arr_remote_path_mappings = mock.Mock()
        self.ensure_arr_discovery_lists_for_app = mock.Mock()
        self.trigger_arr_discovery_kickoff = mock.Mock()
        self.trigger_health_check = mock.Mock()
        self.ensure_readarr_metadata_source = mock.Mock()

        deps = AdapterDependencies(
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            log=self.log,
            ensure_readarr_metadata_source=self.ensure_readarr_metadata_source,
        )

        return ServarrPipelineService(
            log=self.log,
            normalize_url=self.normalize_url,
            detect_arr_api_base=self.detect_arr_api_base,
            ensure_app_auth_settings=self.ensure_app_auth_settings,
            ensure_arr_media_management=self.ensure_arr_media_management,
            ensure_root_folder=self.ensure_root_folder,
            ensure_arr_download_handling=self.ensure_arr_download_handling,
            ensure_arr_quality_upgrade_policy=self.ensure_arr_quality_upgrade_policy,
            ensure_prowlarr_application=self.ensure_prowlarr_application,
            ensure_arr_download_client=self.ensure_arr_download_client,
            ensure_arr_remote_path_mappings=self.ensure_arr_remote_path_mappings,
            ensure_arr_discovery_lists_for_app=self.ensure_arr_discovery_lists_for_app,
            trigger_arr_discovery_kickoff=self.trigger_arr_discovery_kickoff,
            trigger_health_check=self.trigger_health_check,
            adapter_deps=deps,
        )

    def test_pipeline_runs_common_flow_and_readarr_hook(self):
        service = self._service()
        arr_apps = [
            {
                "name": "Sonarr",
                "implementation": "sonarr",
                "url": "http://sonarr:8989/",
                "root_folder": "/media/tv",
            },
            {
                "name": "Readarr",
                "implementation": "readarr",
                "url": "http://readarr:8787/",
                "root_folder": "/media/books",
            },
        ]
        app_keys = {
            "sonarr": "sonarr-key",
            "readarr": "readarr-key",
        }

        service.run(
            cfg={"readarr": {"metadata_source_required": False}},
            arr_apps=arr_apps,
            app_keys=app_keys,
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="prowlarr-key",
            app_auth_cfg={"fail_on_error": False},
            arr_media_management_cfg={"enabled": True},
            arr_download_handling_cfg={"enabled": True},
            arr_quality_upgrade_cfg={"enabled": True},
            qbit_cfg={"url": "http://qbittorrent:8080"},
            qb_user="admin",
            qb_pass="secret",
            sab_cfg={"url": "http://sabnzbd:8080"},
            sab_username="sab",
            sab_password="sabpw",
            sab_remote_path_mappings=[{"remote_path": "/a", "host_path": "/b"}],
            run_cfg=ServarrRunConfig(
                configure_arr_media_management=True,
                configure_arr_download_handling=True,
                configure_arr_quality_upgrade=True,
                configure_arr_discovery_lists=True,
                configure_qbit_arr_clients=True,
                qbit_login_ok=True,
                configure_sab_arr_clients=True,
                sab_api_key="sab-key",
                refresh_health_after_bootstrap=True,
            ),
        )

        self.assertEqual(self.ensure_readarr_metadata_source.call_count, 1)
        self.assertEqual(self.ensure_root_folder.call_count, 2)
        self.assertEqual(self.ensure_arr_download_client.call_count, 4)
        self.assertEqual(self.ensure_arr_remote_path_mappings.call_count, 2)
        self.assertEqual(self.trigger_health_check.call_count, 2)

    def test_pipeline_respects_fail_on_error_auth(self):
        service = self._service()
        self.ensure_app_auth_settings.side_effect = RuntimeError("auth failed")

        with self.assertRaises(RuntimeError):
            service.run(
                cfg={},
                arr_apps=[
                    {
                        "name": "Sonarr",
                        "implementation": "sonarr",
                        "url": "http://sonarr:8989",
                        "root_folder": "/media/tv",
                    }
                ],
                app_keys={"sonarr": "sonarr-key"},
                prowlarr_url="http://prowlarr:9696",
                prowlarr_key="prowlarr-key",
                app_auth_cfg={"fail_on_error": True},
                arr_media_management_cfg={},
                arr_download_handling_cfg={},
                arr_quality_upgrade_cfg={},
                qbit_cfg={},
                qb_user="",
                qb_pass="",
                sab_cfg={},
                sab_username="",
                sab_password="",
                sab_remote_path_mappings=[],
                run_cfg=ServarrRunConfig(
                    configure_arr_media_management=False,
                    configure_arr_download_handling=False,
                    configure_arr_quality_upgrade=False,
                    configure_arr_discovery_lists=False,
                    configure_qbit_arr_clients=False,
                    qbit_login_ok=False,
                    configure_sab_arr_clients=False,
                    sab_api_key="",
                    refresh_health_after_bootstrap=False,
                ),
            )


if __name__ == "__main__":
    unittest.main()
