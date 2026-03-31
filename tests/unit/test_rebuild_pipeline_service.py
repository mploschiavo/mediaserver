import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_pipeline_service import (  # noqa: E402
    RebuildPipelineConfig,
    RebuildPipelineService,
)


class RebuildPipelineServiceTests(unittest.TestCase):
    def _svc(self, run_script):
        return RebuildPipelineService(
            cfg=RebuildPipelineConfig(
                namespace="media-stack",
                root_dir=ROOT,
                prepare_host_root="/srv/media-stack",
                enable_unpackerr="1",
                config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
            ),
            info=mock.Mock(),
            run_script=run_script,
        )

    def test_prepare_host_directories_skips_non_legacy(self):
        svc = self._svc(mock.Mock())
        self.assertFalse(svc.prepare_host_directories("dynamic-pvc"))

    def test_generate_secrets_runs_script(self):
        run_script = mock.Mock()
        svc = self._svc(run_script)
        svc.generate_secrets()
        run_script.assert_called_once()


if __name__ == "__main__":
    unittest.main()
