import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.servarr_adapters import (  # noqa: E402
    AdapterDependencies,
    AdapterRegistry,
    AppBootstrapContext,
    noop_before_common_steps,
)
from bootstrap_services.apps.servarr.config_models import ServarrAppConfig  # noqa: E402
from bootstrap_services.apps.servarr.pipeline_service import (  # noqa: E402
    ClientAuth,
    ServarrPipelineInputs,
    ServarrPipelineService,
    ServarrRunConfig,
)


class ServarrAdapterRegistryTests(unittest.TestCase):
    def test_registry_defaults_to_noop_when_no_hook_manifest_loaded(self):
        deps = AdapterDependencies(
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            log=mock.Mock(),
            ensure_readarr_metadata_source=mock.Mock(),
        )
        registry = AdapterRegistry.from_config({})
        hook = registry.before_common_steps_for("readarr")
        self.assertIs(hook, noop_before_common_steps)
        hook(
            deps,
            AppBootstrapContext(
                cfg={},
                app_cfg={"implementation": "readarr"},
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="abc",
            ),
        )
        deps.ensure_readarr_metadata_source.assert_not_called()

    def test_registry_can_enable_readarr_hook_via_manifest_mapping(self):
        deps = AdapterDependencies(
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            log=mock.Mock(),
            ensure_readarr_metadata_source=mock.Mock(),
        )
        registry = AdapterRegistry.from_config(
            {
                "before_common_steps": {
                    "readarr": "bootstrap_services.servarr_adapters:readarr_before_common_steps",
                }
            }
        )
        hook = registry.before_common_steps_for("readarr")
        hook(
            deps,
            AppBootstrapContext(
                cfg={},
                app_cfg={"implementation": "readarr"},
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="abc",
            ),
        )
        deps.ensure_readarr_metadata_source.assert_called_once()

    def test_registry_invalid_hook_spec_raises(self):
        with self.assertRaises(ValueError):
            AdapterRegistry.from_config(
                {
                    "before_common_steps": {
                        "readarr": "not-a-valid-spec",
                    }
                }
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

    def _base_inputs(self, arr_apps, app_keys, adapter_hooks_cfg=None):
        resolved_hooks = adapter_hooks_cfg
        if resolved_hooks is None:
            resolved_hooks = {
                "before_common_steps": {
                    "readarr": "bootstrap_services.servarr_adapters:readarr_before_common_steps",
                }
            }
        return ServarrPipelineInputs(
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
            qbit_auth=ClientAuth(username="admin", password="secret"),
            sab_cfg={"url": "http://sabnzbd:8080"},
            sab_auth=ClientAuth(username="sab", password="sabpw"),
            sab_remote_path_mappings=[{"remote_path": "/a", "host_path": "/b"}],
            adapter_hooks_cfg=resolved_hooks,
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

        service.run(self._base_inputs(arr_apps, app_keys))

        self.assertEqual(self.ensure_readarr_metadata_source.call_count, 1)
        self.assertEqual(self.ensure_root_folder.call_count, 2)
        self.assertEqual(self.ensure_arr_download_client.call_count, 4)
        self.assertEqual(self.ensure_arr_remote_path_mappings.call_count, 2)
        self.assertEqual(self.trigger_health_check.call_count, 2)

    def test_pipeline_allows_reflection_override_for_readarr(self):
        service = self._service()
        arr_apps = [
            {
                "name": "Readarr",
                "implementation": "readarr",
                "url": "http://readarr:8787/",
                "root_folder": "/media/books",
            },
        ]
        app_keys = {"readarr": "readarr-key"}
        override = {
            "before_common_steps": {
                "readarr": "bootstrap_services.servarr_adapters:noop_before_common_steps"
            }
        }

        service.run(self._base_inputs(arr_apps, app_keys, adapter_hooks_cfg=override))
        self.ensure_readarr_metadata_source.assert_not_called()

    def test_pipeline_respects_fail_on_error_auth(self):
        service = self._service()
        self.ensure_app_auth_settings.side_effect = RuntimeError("auth failed")

        with self.assertRaises(RuntimeError):
            service.run(
                ServarrPipelineInputs(
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
                    qbit_auth=ClientAuth(),
                    sab_cfg={},
                    sab_auth=ClientAuth(),
                    sab_remote_path_mappings=[],
                    adapter_hooks_cfg={},
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
            )

    def test_pipeline_respects_capability_flags(self):
        service = self._service()
        app = ServarrAppConfig.from_dict(
            {
                "name": "Readarr",
                "implementation": "readarr",
                "url": "http://readarr:8787/",
                "root_folder": "/media/books",
                "capabilities": {
                    "supports_auth": False,
                    "supports_media_management": False,
                    "supports_root_folder": False,
                    "supports_download_handling": False,
                    "supports_quality_upgrade": False,
                    "supports_prowlarr_application": False,
                    "supports_download_clients": False,
                    "supports_remote_path_mappings": False,
                    "supports_discovery_lists": False,
                    "supports_health_check": False,
                },
            }
        )

        service.run(self._base_inputs([app], {"readarr": "readarr-key"}))

        self.ensure_app_auth_settings.assert_not_called()
        self.ensure_arr_media_management.assert_not_called()
        self.ensure_root_folder.assert_not_called()
        self.ensure_arr_download_handling.assert_not_called()
        self.ensure_arr_quality_upgrade_policy.assert_not_called()
        self.ensure_prowlarr_application.assert_not_called()
        self.ensure_arr_download_client.assert_not_called()
        self.ensure_arr_remote_path_mappings.assert_not_called()
        self.ensure_arr_discovery_lists_for_app.assert_not_called()
        self.trigger_arr_discovery_kickoff.assert_not_called()
        self.trigger_health_check.assert_not_called()

    def test_pipeline_rejects_invalid_adapter_class_config_shape(self):
        service = self._service()
        arr_apps = [
            {
                "name": "Sonarr",
                "implementation": "sonarr",
                "url": "http://sonarr:8989/",
                "root_folder": "/media/tv",
            }
        ]
        with self.assertRaises(ValueError):
            service.run(
                self._base_inputs(
                    arr_apps,
                    {"sonarr": "sonarr-key"},
                    adapter_hooks_cfg={"adapter_classes": "invalid"},
                )
            )


if __name__ == "__main__":
    unittest.main()
