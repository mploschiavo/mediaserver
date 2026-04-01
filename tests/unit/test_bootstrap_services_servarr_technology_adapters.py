import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.config_models import ServarrAppConfig  # noqa: E402
from bootstrap_services.plugin_manifest_loader import (  # noqa: E402
    build_adapter_hook_defaults,
    load_plugin_manifests,
)
from bootstrap_services.servarr_adapters import (  # noqa: E402
    AdapterDependencies,
    noop_before_common_steps,
)
from bootstrap_services.servarr_technologies import (  # noqa: E402
    GenericServarrAdapter,
    LidarrAdapter,
    RadarrAdapter,
    ReadarrAdapter,
    ServarrAdapterContext,
    ServarrAdapterDependencies,
    ServarrAdapterFactory,
    SonarrAdapter,
)
from bootstrap_services.servarr_types import ClientAuth, ServarrRunConfig  # noqa: E402


class ServarrTechnologyAdaptersTests(unittest.TestCase):
    def _deps(self):
        return ServarrAdapterDependencies(
            log=mock.Mock(),
            normalize_url=mock.Mock(side_effect=lambda value: value.rstrip("/")),
            detect_arr_api_base=mock.Mock(return_value="/api/v1"),
            ensure_app_auth_settings=mock.Mock(),
            ensure_arr_media_management=mock.Mock(),
            ensure_root_folder=mock.Mock(),
            ensure_arr_download_handling=mock.Mock(),
            ensure_arr_quality_upgrade_policy=mock.Mock(),
            ensure_prowlarr_application=mock.Mock(),
            ensure_arr_download_client=mock.Mock(),
            ensure_arr_remote_path_mappings=mock.Mock(),
            ensure_arr_discovery_lists_for_app=mock.Mock(),
            trigger_arr_discovery_kickoff=mock.Mock(),
            trigger_health_check=mock.Mock(),
        )

    def _adapter_deps(self):
        return AdapterDependencies(
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            log=mock.Mock(),
            ensure_readarr_metadata_source=mock.Mock(),
        )

    def _context(self, implementation: str) -> ServarrAdapterContext:
        app_model = ServarrAppConfig.from_dict(
            {
                "name": implementation.capitalize(),
                "implementation": implementation,
                "url": f"http://{implementation}:8989",
                "root_folder": f"/media/{implementation}",
            }
        )
        return ServarrAdapterContext(
            cfg={},
            app_model=app_model,
            app_payload=dict(app_model.raw),
            app_key=f"{implementation}-key",
            app_auth_cfg={},
            arr_media_management_cfg={},
            arr_download_handling_cfg={},
            arr_quality_upgrade_cfg={},
            qbit_cfg={},
            qbit_auth=ClientAuth(),
            sab_cfg={},
            sab_auth=ClientAuth(),
            sab_remote_path_mappings=[],
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="prowlarr-key",
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

    def test_factory_maps_implementations_to_specific_adapters(self):
        factory = ServarrAdapterFactory(
            deps=self._deps(),
            adapter_deps=self._adapter_deps(),
        )
        self.assertIsInstance(
            factory.create(self._context("sonarr"), noop_before_common_steps),
            SonarrAdapter,
        )
        self.assertIsInstance(
            factory.create(self._context("radarr"), noop_before_common_steps),
            RadarrAdapter,
        )
        self.assertIsInstance(
            factory.create(self._context("lidarr"), noop_before_common_steps),
            LidarrAdapter,
        )
        self.assertIsInstance(
            factory.create(self._context("readarr"), noop_before_common_steps),
            ReadarrAdapter,
        )
        with self.assertRaises(ValueError):
            factory.create(self._context("customarr"), noop_before_common_steps)

    def test_adapter_lifecycle_calls_core_steps(self):
        deps = self._deps()
        factory = ServarrAdapterFactory(
            deps=deps,
            adapter_deps=self._adapter_deps(),
        )
        adapter = factory.create(self._context("sonarr"), noop_before_common_steps)
        adapter.load()
        adapter.precheck()
        adapter.prepare()
        adapter.configure()
        adapter.ensure()

        deps.detect_arr_api_base.assert_called_once()
        deps.ensure_app_auth_settings.assert_called_once()

    def test_factory_supports_reflection_override_for_adapter_class(self):
        factory = ServarrAdapterFactory(
            deps=self._deps(),
            adapter_deps=self._adapter_deps(),
            adapter_class_specs={
                "sonarr": "bootstrap_services.servarr_technologies.generic:GenericServarrAdapter"
            },
        )
        adapter = factory.create(self._context("sonarr"), noop_before_common_steps)
        self.assertIsInstance(adapter, GenericServarrAdapter)

    def test_factory_rejects_disabled_specific_mapping(self):
        factory = ServarrAdapterFactory(
            deps=self._deps(),
            adapter_deps=self._adapter_deps(),
            adapter_class_specs={"sonarr": ""},
        )
        with self.assertRaises(ValueError):
            factory.create(self._context("sonarr"), noop_before_common_steps)

    def test_factory_requires_explicit_mapping_for_custom_impl_module(self):
        module_name = "bootstrap_services.servarr_technologies.custom_arr"
        fake_module = types.ModuleType(module_name)

        class CustomArrAdapter(GenericServarrAdapter):
            pass

        fake_module.CustomArrAdapter = CustomArrAdapter

        with mock.patch.dict(sys.modules, {module_name: fake_module}):
            adapter = ServarrAdapterFactory(
                deps=self._deps(),
                adapter_deps=self._adapter_deps(),
                adapter_class_specs={
                    "custom-arr": "bootstrap_services.servarr_technologies.custom_arr:CustomArrAdapter"
                },
            ).create(self._context("custom-arr"), noop_before_common_steps)

        self.assertIsInstance(adapter, CustomArrAdapter)

    def test_removing_sonarr_mapping_keeps_other_technologies_functional(self):
        defaults = build_adapter_hook_defaults(load_plugin_manifests())
        filtered_specs = {
            key: value
            for key, value in (defaults.adapter_classes or {}).items()
            if str(key).strip().lower() != "sonarr"
        }
        self.assertNotIn("sonarr", filtered_specs)

        factory = ServarrAdapterFactory(
            deps=self._deps(),
            adapter_deps=self._adapter_deps(),
            adapter_class_specs=filtered_specs,
        )

        self.assertIsInstance(
            factory.create(self._context("radarr"), noop_before_common_steps),
            RadarrAdapter,
        )
        with self.assertRaises(ValueError):
            factory.create(self._context("sonarr"), noop_before_common_steps)

    def test_removing_radarr_mapping_keeps_other_technologies_functional(self):
        defaults = build_adapter_hook_defaults(load_plugin_manifests())
        filtered_specs = {
            key: value
            for key, value in (defaults.adapter_classes or {}).items()
            if str(key).strip().lower() != "radarr"
        }
        self.assertNotIn("radarr", filtered_specs)

        factory = ServarrAdapterFactory(
            deps=self._deps(),
            adapter_deps=self._adapter_deps(),
            adapter_class_specs=filtered_specs,
        )

        self.assertIsInstance(
            factory.create(self._context("sonarr"), noop_before_common_steps),
            SonarrAdapter,
        )
        with self.assertRaises(ValueError):
            factory.create(self._context("radarr"), noop_before_common_steps)

    def test_removing_readarr_mapping_keeps_other_technologies_functional(self):
        defaults = build_adapter_hook_defaults(load_plugin_manifests())
        filtered_specs = {
            key: value
            for key, value in (defaults.adapter_classes or {}).items()
            if str(key).strip().lower() != "readarr"
        }
        self.assertNotIn("readarr", filtered_specs)

        factory = ServarrAdapterFactory(
            deps=self._deps(),
            adapter_deps=self._adapter_deps(),
            adapter_class_specs=filtered_specs,
        )

        self.assertIsInstance(
            factory.create(self._context("sonarr"), noop_before_common_steps),
            SonarrAdapter,
        )
        with self.assertRaises(ValueError):
            factory.create(self._context("readarr"), noop_before_common_steps)


if __name__ == "__main__":
    unittest.main()
