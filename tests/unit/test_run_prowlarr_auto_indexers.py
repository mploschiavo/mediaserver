import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "run_prowlarr_auto_indexers",
    ROOT / "scripts" / "run_prowlarr_auto_indexers.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class _FakeKube:
    def run(self, *_args, **_kwargs):
        raise AssertionError("kube.run should not be used in this unit test")


class ProwlarrAutoIndexerRunnerUnitTests(unittest.TestCase):
    def test_timeout_seconds_parsing(self):
        self.assertEqual(
            MODULE.AutoIndexerConfig(
                namespace="media-stack",
                timeout_raw="20m",
                heartbeat_interval=15,
                prepare_host_root="/srv/media-stack",
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
            root_dir=ROOT,
        )
        runner = MODULE.ProwlarrAutoIndexerRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        rendered = runner.manifest_overrides(
            "namespace: media-stack\npath: /srv/media-stack\n"
        )
        self.assertIn("namespace: media-stack-dev", rendered)
        self.assertIn("/mnt/media-dev", rendered)
        self.assertNotIn("/srv/media-stack", rendered)

    def test_load_bootstrap_script_files_parses_shared_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "scripts" / "lib" / "bootstrap-script-configmap.sh"
            helper.parent.mkdir(parents=True, exist_ok=True)
            helper.write_text(
                "\n".join(
                    [
                        "BOOTSTRAP_SCRIPT_CONFIGMAP_FILES=(",
                        '  "alpha.py|scripts/alpha.py"',
                        '  "beta.py|scripts/beta.py"',
                        ")",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = MODULE.AutoIndexerConfig(
                namespace="media-stack",
                timeout_raw="20m",
                heartbeat_interval=15,
                prepare_host_root="/srv/media-stack",
                root_dir=root,
            )
            runner = MODULE.ProwlarrAutoIndexerRunner(
                cfg=cfg,
                kube=_FakeKube(),
                tracker=MODULE.PhaseTracker(),
            )

            entries = runner._load_bootstrap_script_files()
            self.assertEqual(
                entries,
                [
                    ("alpha.py", Path("scripts/alpha.py")),
                    ("beta.py", Path("scripts/beta.py")),
                ],
            )


if __name__ == "__main__":
    unittest.main()
