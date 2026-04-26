import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

MODULE = importlib.import_module(
    "media_stack.services.apps.prowlarr.cli.run_prowlarr_auto_indexers_main"
)


class _FakeKube:
    def run(self, *_args, **_kwargs):
        raise AssertionError("kube.run should not be used in this unit test")


class ProwlarrAutoIndexerRunnerUnitTests(unittest.TestCase):
    def test_parse_config_uses_namespace_env_by_default(self):
        with mock.patch.dict("os.environ", {"NAMESPACE": "media-dev"}, clear=False):
            cfg = MODULE.parse_config([])

        self.assertEqual(cfg.namespace, "media-dev")

    def test_timeout_seconds_parsing(self):
        self.assertEqual(
            MODULE.AutoIndexerConfig(
                namespace="media-stack",
                timeout_raw="20m",
                heartbeat_interval=15,
                prepare_host_root="/srv/media-stack",
                bootstrap_runner_image="registry.example/media-stack-controller:latest",
                exclude_name_tokens=[],
                reputation_cfg={},
                root_dir=ROOT,
            ).timeout_seconds,
            1200,
        )
        self.assertEqual(
            MODULE.AutoIndexerConfig(
                namespace="media-stack",
                timeout_raw="90s",
                heartbeat_interval=15,
                prepare_host_root="/srv/media-stack",
                bootstrap_runner_image="registry.example/media-stack-controller:latest",
                exclude_name_tokens=[],
                reputation_cfg={},
                root_dir=ROOT,
            ).timeout_seconds,
            90,
        )
        self.assertEqual(
            MODULE.AutoIndexerConfig(
                namespace="media-stack",
                timeout_raw="2h",
                heartbeat_interval=15,
                prepare_host_root="/srv/media-stack",
                bootstrap_runner_image="registry.example/media-stack-controller:latest",
                exclude_name_tokens=[],
                reputation_cfg={},
                root_dir=ROOT,
            ).timeout_seconds,
            7200,
        )

    def test_manifest_overrides_replaces_namespace_and_host_root(self):
        cfg = MODULE.AutoIndexerConfig(
            namespace="media-stack-dev",
            timeout_raw="20m",
            heartbeat_interval=15,
            prepare_host_root="/mnt/media-dev",
            bootstrap_runner_image="registry.example/custom/bootstrap:dev",
            exclude_name_tokens=[],
            reputation_cfg={},
            root_dir=ROOT,
        )
        runner = MODULE.ProwlarrAutoIndexerRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        rendered = runner.manifest_overrides(
            "namespace: media-stack\n"
            "image: harbor.iomio.io/library/media-stack-controller:latest\n"
            "path: /srv/media-stack\n"
        )
        self.assertIn("namespace: media-stack-dev", rendered)
        self.assertIn("/mnt/media-dev", rendered)
        self.assertIn("image: registry.example/custom/bootstrap:dev", rendered)
        self.assertNotIn("/srv/media-stack", rendered)


if __name__ == "__main__":
    unittest.main()
