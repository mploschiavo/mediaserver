import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.bootstrap_runner_service import (  # noqa: E402
    BootstrapRunnerDependencies,
    BootstrapRunnerService,
    BootstrapRuntime,
)
from bootstrap_services.config_models import ServarrAppConfig  # noqa: E402
from bootstrap_services.enums import BootstrapMode, RunnerOperation  # noqa: E402
from bootstrap_services.runner_operations_service import RunnerOperationRegistry  # noqa: E402


class BootstrapRunnerServiceTests(unittest.TestCase):
    def _deps(self):
        operation_mocks = {
            RunnerOperation.ENSURE_APP_AUTH_SETTINGS.value: mock.Mock(),
            RunnerOperation.QBIT_LOGIN.value: mock.Mock(),
            RunnerOperation.READ_SABNZBD_API_KEY.value: mock.Mock(return_value=""),
            RunnerOperation.ENSURE_SABNZBD_DEFAULTS.value: mock.Mock(),
            RunnerOperation.ENSURE_SABNZBD_CATEGORIES.value: mock.Mock(),
            RunnerOperation.SETUP_QBIT_CATEGORIES.value: mock.Mock(),
            RunnerOperation.RUN_SERVARR_PIPELINE.value: mock.Mock(),
            RunnerOperation.ENSURE_BAZARR_INTEGRATION.value: mock.Mock(),
            RunnerOperation.CONFIGURE_JELLYSEERR.value: mock.Mock(),
            RunnerOperation.ENSURE_JELLYFIN_LIVETV.value: mock.Mock(),
            RunnerOperation.ENSURE_JELLYFIN_LIBRARIES.value: mock.Mock(),
            RunnerOperation.ENSURE_JELLYFIN_PLUGINS.value: mock.Mock(),
            RunnerOperation.ENSURE_JELLYFIN_PLAYBACK.value: mock.Mock(),
            RunnerOperation.ENSURE_JELLYFIN_HOME_RAILS.value: mock.Mock(),
            RunnerOperation.ENSURE_JELLYFIN_AUTO_COLLECTIONS.value: mock.Mock(),
            RunnerOperation.ENFORCE_DISK_GUARDRAILS.value: mock.Mock(),
            RunnerOperation.RUN_MEDIA_HYGIENE.value: mock.Mock(),
            RunnerOperation.ENSURE_JELLYFIN_PREWARM.value: mock.Mock(),
            RunnerOperation.ENSURE_MAINTAINERR_POLICY.value: mock.Mock(),
            RunnerOperation.ENSURE_MAINTAINERR_INTEGRATIONS.value: mock.Mock(),
            RunnerOperation.ENSURE_HOMEPAGE_SERVICES.value: mock.Mock(),
            RunnerOperation.ENSURE_PROWLARR_READY.value: mock.Mock(return_value="/api/v1"),
            RunnerOperation.ENSURE_PROWLARR_FLARESOLVERR_PROXY.value: mock.Mock(),
            RunnerOperation.ENSURE_PROWLARR_INDEXER.value: mock.Mock(),
            RunnerOperation.AUTO_ADD_TESTED_INDEXERS.value: mock.Mock(),
            RunnerOperation.TRIGGER_PROWLARR_SYNC.value: mock.Mock(),
            RunnerOperation.SYNC_ARR_INDEXERS_FROM_PROWLARR.value: mock.Mock(),
            RunnerOperation.RUN_PROWLARR_INDEXER_PIPELINE.value: mock.Mock(),
        }
        deps = BootstrapRunnerDependencies(
            log=mock.Mock(),
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            normalize_url=lambda value: value.rstrip("/"),
            wait_for_service=mock.Mock(),
            operations=RunnerOperationRegistry(handlers=operation_mocks),
        )
        deps.operation_mocks = operation_mocks  # type: ignore[attr-defined]
        return deps

    def _runtime(self, **overrides):
        defaults = dict(
            mode=BootstrapMode.FULL,
            cfg={},
            config_root="/srv-config",
            wait_timeout=30,
            arr_apps_raw=[],
            arr_apps=[],
            app_keys={},
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="key",
            qbit_cfg={},
            sab_cfg={},
            torrent_client_key="qbittorrent",
            usenet_client_key="sabnzbd",
            arr_media_management_cfg={},
            arr_download_handling_cfg={},
            arr_quality_upgrade_cfg={},
            app_auth_cfg={},
            adapter_hooks_cfg={
                "app_service_classes": {
                    "prowlarr_service": "bootstrap_services.prowlarr_service:ProwlarrService"
                },
                "service_technology_map": {"prowlarr_service": "prowlarr"},
                "media_server_operation_plans": {
                    "jellyfin": {
                        "prewarm_mode": {
                            "steps": [
                                {
                                    "operation": "ensure_jellyfin_prewarm",
                                    "args": ["cfg", "config_root"],
                                }
                            ]
                        },
                        "home_rails_mode": {
                            "steps": [
                                {
                                    "operation": "ensure_jellyfin_home_rails",
                                    "args": ["cfg", "config_root"],
                                }
                            ]
                        },
                        "pre_servarr_steps": {"steps": []},
                        "post_servarr_pre_hygiene_steps": {"steps": []},
                        "post_servarr_post_hygiene_steps": {"steps": []},
                    }
                },
                "runner_operation_plans": {
                    "precheck_steps": {
                        "steps": [
                            {
                                "operation": "ensure_prowlarr_ready",
                                "args": [
                                    "cfg",
                                    "prowlarr_url",
                                    "prowlarr_key",
                                    "app_auth_cfg",
                                    "wait_timeout",
                                ],
                                "enabled_when_attr": "prowlarr_url",
                            },
                            {
                                "operation": "ensure_maintainerr_policy",
                                "args": ["cfg", "config_root"],
                                "enabled_attr": "configure_maintainerr_policy",
                                "required_attr": "maintainerr_required",
                            },
                            {
                                "operation": "ensure_homepage_services_config",
                                "args": ["cfg", "config_root"],
                                "enabled_attr": "configure_homepage_services",
                                "required_attr": "homepage_required",
                            },
                        ]
                    },
                    "post_servarr_pre_media_steps": {
                        "steps": [
                            {
                                "operation": "ensure_bazarr_arr_integration",
                                "args": [
                                    "cfg",
                                    "config_root",
                                    "arr_apps_raw",
                                    "app_keys",
                                    "wait_timeout",
                                ],
                                "enabled_attr": "configure_bazarr_integration",
                                "required_attr": "bazarr_required",
                            },
                            {
                                "operation": "configure_jellyseerr",
                                "args": [
                                    "cfg",
                                    "arr_apps_raw",
                                    "app_keys",
                                    "config_root",
                                    "wait_timeout",
                                ],
                                "enabled_attr": "configure_jellyseerr_services",
                                "required_attr": "jellyseerr_required",
                            },
                            {
                                "operation": "ensure_maintainerr_integrations",
                                "args": ["cfg", "config_root", "arr_apps_raw", "wait_timeout"],
                                "enabled_attr": "configure_maintainerr_integrations",
                                "required_attr": "maintainerr_integrations_required",
                            },
                        ]
                    },
                    "post_servarr_post_media_steps": {
                        "steps": [
                            {
                                "operation": "enforce_disk_guardrails",
                                "args": ["cfg", "config_root", "qbit_cfg", "qb_user", "qb_pass"],
                                "enabled_attr": "configure_disk_guardrails",
                                "required_attr": "disk_guardrails_required",
                            },
                            {
                                "operation": "run_media_hygiene",
                                "args": [
                                    "cfg",
                                    "config_root",
                                    "arr_apps_raw",
                                    "app_keys",
                                    "qbit_cfg",
                                    "qb_user",
                                    "qb_pass",
                                ],
                                "enabled_attr": "configure_media_hygiene",
                                "required_attr": "media_hygiene_required",
                            },
                        ]
                    },
                    "indexer_steps": {
                        "steps": [
                            {
                                "operation": "run_prowlarr_indexer_pipeline",
                                "args": [
                                    "cfg",
                                    "prowlarr_url",
                                    "prowlarr_key",
                                    "wait_timeout",
                                    "prowlarr_indexers",
                                    "auto_indexers",
                                    "trigger_sync",
                                    "arr_apps_raw",
                                    "app_keys",
                                ],
                            }
                        ]
                    },
                }
            },
            prowlarr_indexers=[],
            sab_remote_path_mappings=[],
            qb_user="u",
            qb_pass="p",
            sab_username="",
            sab_password="",
            auto_indexers=False,
            trigger_sync=False,
            fully_preconfigured=False,
            configure_qbit_arr_clients=False,
            configure_sab_arr_clients=False,
            configure_arr_media_management=False,
            configure_arr_download_handling=False,
            configure_arr_quality_upgrade=False,
            configure_arr_discovery_lists=False,
            set_qbit_categories=False,
            qbit_login_required=False,
            refresh_health_after_bootstrap=False,
            configure_maintainerr_policy=False,
            maintainerr_required=False,
            configure_maintainerr_integrations=False,
            maintainerr_integrations_required=False,
            configure_homepage_services=False,
            homepage_required=False,
            configure_bazarr_integration=False,
            bazarr_required=False,
            configure_jellyseerr_services=False,
            jellyseerr_required=False,
            configure_jellyfin_livetv=False,
            jellyfin_livetv_required=False,
            configure_jellyfin_libraries=False,
            jellyfin_libraries_required=False,
            configure_jellyfin_plugins=False,
            jellyfin_plugins_required=False,
            configure_jellyfin_playback=False,
            jellyfin_playback_required=False,
            configure_jellyfin_home_rails=False,
            jellyfin_home_rails_required=False,
            configure_auto_collections=False,
            auto_collections_required=False,
            configure_disk_guardrails=False,
            disk_guardrails_required=False,
            configure_media_hygiene=False,
            media_hygiene_required=False,
            configure_jellyfin_prewarm=False,
            jellyfin_prewarm_required=False,
        )
        defaults.update(overrides)
        return BootstrapRuntime(**defaults)

    def test_prewarm_mode_short_circuit(self):
        deps = self._deps()
        runner = BootstrapRunnerService(deps=deps)
        runtime = self._runtime(mode=BootstrapMode.MEDIA_SERVER_PREWARM)
        runner.run(runtime)
        deps.operation_mocks[RunnerOperation.ENSURE_JELLYFIN_PREWARM.value].assert_called_once()  # type: ignore[attr-defined]
        deps.operation_mocks[RunnerOperation.RUN_SERVARR_PIPELINE.value].assert_not_called()  # type: ignore[attr-defined]

    def test_media_hygiene_mode_waits_and_runs_hygiene(self):
        deps = self._deps()
        runner = BootstrapRunnerService(deps=deps)
        arr_app = ServarrAppConfig.from_dict(
            {
                "name": "Sonarr",
                "implementation": "sonarr",
                "url": "http://sonarr:8989",
                "root_folder": "/media/tv",
            }
        )
        runtime = self._runtime(
            mode=BootstrapMode.MEDIA_HYGIENE,
            arr_apps=[arr_app],
            arr_apps_raw=[arr_app.raw],
        )
        runner.run(runtime)
        deps.wait_for_service.assert_called()
        deps.operation_mocks[RunnerOperation.RUN_MEDIA_HYGIENE.value].assert_called_once()  # type: ignore[attr-defined]
        deps.operation_mocks[RunnerOperation.RUN_SERVARR_PIPELINE.value].assert_not_called()  # type: ignore[attr-defined]

    def test_full_mode_runs_pipeline_and_optional_sync(self):
        deps = self._deps()
        runner = BootstrapRunnerService(deps=deps)
        runtime = self._runtime(trigger_sync=True)
        runner.run(runtime)
        deps.operation_mocks[RunnerOperation.RUN_SERVARR_PIPELINE.value].assert_called_once()  # type: ignore[attr-defined]
        deps.operation_mocks[RunnerOperation.RUN_PROWLARR_INDEXER_PIPELINE.value].assert_called_once_with(  # type: ignore[attr-defined]
            runtime.cfg,
            "http://prowlarr:9696",
            "key",
            runtime.wait_timeout,
            runtime.prowlarr_indexers,
            runtime.auto_indexers,
            True,
            runtime.arr_apps_raw,
            runtime.app_keys,
        )

    def test_runner_tracks_lifecycle_states(self):
        deps = self._deps()
        runner = BootstrapRunnerService(deps=deps)
        runtime = self._runtime()
        runner.run(runtime)
        deps.operation_mocks[RunnerOperation.ENSURE_PROWLARR_READY.value].assert_called_once()  # type: ignore[attr-defined]
        self.assertIsNotNone(runner.lifecycle_manager)
        prowlarr_state = runner.lifecycle_manager.state("prowlarr")
        self.assertIsNotNone(prowlarr_state)
        self.assertTrue(prowlarr_state.loaded)
        self.assertTrue(prowlarr_state.prechecked)
        self.assertEqual(prowlarr_state.status, "ok")

    def test_required_optional_step_still_fails_hard(self):
        deps = self._deps()
        deps.operation_mocks[RunnerOperation.CONFIGURE_JELLYSEERR.value].side_effect = RuntimeError(  # type: ignore[attr-defined]
            "boom"
        )
        runner = BootstrapRunnerService(deps=deps)
        runtime = self._runtime(
            configure_jellyseerr_services=True,
            jellyseerr_required=True,
        )
        with self.assertRaises(RuntimeError):
            runner.run(runtime)

    def test_runner_configures_maintainerr_integrations_when_enabled(self):
        deps = self._deps()
        runner = BootstrapRunnerService(deps=deps)
        runtime = self._runtime(
            configure_maintainerr_integrations=True,
            arr_apps_raw=[{"implementation": "sonarr", "url": "http://sonarr:8989"}],
        )

        runner.run(runtime)

        deps.operation_mocks[RunnerOperation.ENSURE_MAINTAINERR_INTEGRATIONS.value].assert_called_once_with(  # type: ignore[attr-defined]
            runtime.cfg,
            runtime.config_root,
            runtime.arr_apps_raw,
            runtime.wait_timeout,
        )

    def test_runner_configures_flaresolverr_proxy_when_enabled(self):
        deps = self._deps()
        runner = BootstrapRunnerService(deps=deps)
        runtime = self._runtime(
            cfg={
                "flaresolverr": {
                    "enabled": True,
                    "required": False,
                    "url": "http://flaresolverr:8191",
                }
            }
        )

        runner.run(runtime)

        deps.operation_mocks[
            RunnerOperation.RUN_PROWLARR_INDEXER_PIPELINE.value
        ].assert_called_once_with(  # type: ignore[attr-defined]
            runtime.cfg,
            runtime.prowlarr_url,
            runtime.prowlarr_key,
            runtime.wait_timeout,
            runtime.prowlarr_indexers,
            runtime.auto_indexers,
            runtime.trigger_sync,
            runtime.arr_apps_raw,
            runtime.app_keys,
        )

    def test_runner_canonicalizes_lifecycle_keys_from_aliases(self):
        deps = self._deps()
        runner = BootstrapRunnerService(deps=deps)
        runtime = self._runtime(
            torrent_client_key="qbit",
            usenet_client_key="sab",
            adapter_hooks_cfg={
                "technology_aliases": {
                    "qbit": "qbittorrent",
                    "sab": "sabnzbd",
                },
                "download_client_adapter_classes": {
                    "qbit": "bootstrap_services.download_client_adapters.qbittorrent:QbittorrentDownloadClientAdapter",
                    "sab": "bootstrap_services.download_client_adapters.sabnzbd:SabnzbdDownloadClientAdapter",
                },
                "app_service_classes": {
                    "jellyseerr_service": "bootstrap_services.jellyseerr_service:JellyseerrService"
                },
                "media_server_operation_plans": {
                    "jellyfin": {
                        "prewarm_mode": {"steps": []},
                        "home_rails_mode": {"steps": []},
                        "pre_servarr_steps": {"steps": []},
                        "post_servarr_pre_hygiene_steps": {"steps": []},
                        "post_servarr_post_hygiene_steps": {"steps": []},
                    }
                },
            },
        )
        runner.run(runtime)
        self.assertIsNotNone(runner.lifecycle_manager)
        self.assertIsNotNone(runner.lifecycle_manager.state("qbittorrent"))
        self.assertIsNotNone(runner.lifecycle_manager.state("sabnzbd"))
        self.assertIsNone(runner.lifecycle_manager.state("qbit"))
        self.assertIsNone(runner.lifecycle_manager.state("sab"))


if __name__ == "__main__":
    unittest.main()
