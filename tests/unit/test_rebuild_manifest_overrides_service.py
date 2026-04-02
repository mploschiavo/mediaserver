import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.kubernetes.services.rebuild_manifest_overrides_service import (  # noqa: E402
    RebuildManifestOverridesConfig,
    RebuildManifestOverridesService,
)


class RebuildManifestOverridesServiceTests(unittest.TestCase):
    def _svc(self, run_kubectl=None):
        return RebuildManifestOverridesService(
            cfg=RebuildManifestOverridesConfig(
                namespace="media-stack-dev",
                prepare_host_root="/mnt/media",
                ingress_domain="example.local",
                pvc_storage_class="fast",
            ),
            run_kubectl=run_kubectl or mock.Mock(),
        )

    def test_stream_overrides_namespace_host_root_and_domain(self):
        svc = self._svc()
        rendered = svc.stream_with_manifest_overrides(
            "namespace: media-stack\n"
            "name: media-stack\n"
            "host: jellyfin.local\n"
            "path: /srv/media-stack\n"
            '  STACK_ADMIN_PASSWORD: "media-stack"\n'
        )
        self.assertIn("namespace: media-stack-dev", rendered)
        self.assertIn("name: media-stack-dev", rendered)
        self.assertIn("jellyfin.example.local", rendered)
        self.assertIn("/mnt/media", rendered)
        self.assertIn('STACK_ADMIN_PASSWORD: "media-stack-dev"', rendered)

    def test_apply_manifest_text_calls_kubectl_with_patched_manifest(self):
        run_kubectl = mock.Mock()
        svc = self._svc(run_kubectl=run_kubectl)
        svc.apply_manifest_text_with_overrides(
            "kind: PersistentVolumeClaim\nspec:\n  resources:\n    requests:\n      storage: 1Gi\n"
        )
        run_kubectl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
