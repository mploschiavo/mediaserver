import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.prowlarr import runtime_ops  # noqa: E402


class ProwlarrRuntimeOpsTests(unittest.TestCase):
    def test_run_indexer_pipeline_uses_direct_prowlarr_service_url(self):
        pipeline = mock.Mock()
        pipeline.run.return_value = "ok"
        cfg = {
            "app_auth": {
                "path_prefix_url_base_by_app": {
                    "prowlarr": "/app/prowlarr",
                }
            }
        }
        with mock.patch.object(
            runtime_ops, "_prowlarr_indexer_pipeline_service", return_value=pipeline
        ):
            result = runtime_ops.run_prowlarr_indexer_pipeline(
                cfg=cfg,
                prowlarr_url="http://prowlarr:9696",
                prowlarr_key="key",
                wait_timeout=30,
                prowlarr_indexers=[],
                auto_indexers=False,
                trigger_sync=False,
                arr_apps_raw=[],
                app_keys={},
            )

        self.assertEqual(result, "ok")
        self.assertTrue(pipeline.run.called)
        self.assertEqual(
            pipeline.run.call_args.kwargs.get("prowlarr_url"),
            "http://prowlarr:9696",
        )

    def test_ensure_ready_uses_direct_prowlarr_service_url(self):
        precheck = mock.Mock()
        precheck.ensure_ready.return_value = "/api/v1"
        cfg = {
            "app_auth": {
                "path_prefix_url_base_by_app": {
                    "prowlarr": "/app/prowlarr",
                }
            }
        }
        with mock.patch.object(runtime_ops, "_prowlarr_precheck_service", return_value=precheck):
            api_base = runtime_ops.ensure_prowlarr_ready(
                cfg=cfg,
                prowlarr_url="http://prowlarr:9696",
                prowlarr_key="key",
                app_auth_cfg={"enabled": True},
                wait_timeout=30,
            )

        self.assertEqual(api_base, "/api/v1")
        self.assertTrue(precheck.ensure_ready.called)
        self.assertEqual(
            precheck.ensure_ready.call_args.kwargs.get("prowlarr_url"),
            "http://prowlarr:9696",
        )


if __name__ == "__main__":
    unittest.main()
