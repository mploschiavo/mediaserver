import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.prowlarr.pipeline_service import (  # noqa: E402
    ProwlarrIndexerPipelineService,
)


class ProwlarrIndexerPipelineServiceTests(unittest.TestCase):
    def _service(
        self,
        *,
        ensure_flaresolverr_proxy=None,
        ensure_indexer=None,
        auto_add_tested_indexers=None,
        trigger_sync=None,
        sync_arr_indexers_from_prowlarr=None,
    ):
        return ProwlarrIndexerPipelineService(
            log=mock.Mock(),
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            ensure_flaresolverr_proxy=ensure_flaresolverr_proxy or mock.Mock(),
            ensure_indexer=ensure_indexer or mock.Mock(),
            auto_add_tested_indexers=auto_add_tested_indexers or mock.Mock(),
            trigger_sync=trigger_sync or mock.Mock(),
            sync_arr_indexers_from_prowlarr=sync_arr_indexers_from_prowlarr or mock.Mock(),
        )

    def test_warns_when_flaresolverr_optional_step_fails(self):
        ensure_proxy = mock.Mock(side_effect=RuntimeError("proxy down"))
        service = self._service(ensure_flaresolverr_proxy=ensure_proxy)
        service.run(
            cfg={"flaresolverr": {"enabled": True, "required": False}},
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="key",
            wait_timeout=30,
            prowlarr_indexers=[],
            auto_indexers=False,
            trigger_sync=False,
            arr_apps_raw=[],
            app_keys={},
        )
        ensure_proxy.assert_called_once()
        service.log.assert_called()
        self.assertIn(
            "Prowlarr FlareSolverr proxy: automation skipped", service.log.call_args[0][0]
        )

    def test_raises_when_fail_on_indexer_error_enabled(self):
        ensure_indexer = mock.Mock(side_effect=RuntimeError("bad indexer"))
        service = self._service(ensure_indexer=ensure_indexer)
        with self.assertRaises(RuntimeError):
            service.run(
                cfg={"fail_on_indexer_error": True},
                prowlarr_url="http://prowlarr:9696",
                prowlarr_key="key",
                wait_timeout=30,
                prowlarr_indexers=[{"name": "bad", "implementation": "X"}],
                auto_indexers=False,
                trigger_sync=False,
                arr_apps_raw=[],
                app_keys={},
            )

    def test_runs_auto_and_sync_pipeline(self):
        auto_add = mock.Mock()
        trigger = mock.Mock()
        sync = mock.Mock()
        service = self._service(
            auto_add_tested_indexers=auto_add,
            trigger_sync=trigger,
            sync_arr_indexers_from_prowlarr=sync,
        )
        service.run(
            cfg={"arr_indexer_sync": {"prune_stale_indexers": True}},
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="key",
            wait_timeout=30,
            prowlarr_indexers=[],
            auto_indexers=True,
            trigger_sync=True,
            arr_apps_raw=[{"implementation": "sonarr", "url": "http://sonarr:8989"}],
            app_keys={"sonarr": "sonarr-key"},
        )
        auto_add.assert_called_once()
        trigger.assert_called_once_with("http://prowlarr:9696", "key")
        sync.assert_called_once_with(
            "http://prowlarr:9696",
            "key",
            [{"implementation": "sonarr", "url": "http://sonarr:8989"}],
            {"sonarr": "sonarr-key"},
            True,
        )


if __name__ == "__main__":
    unittest.main()
