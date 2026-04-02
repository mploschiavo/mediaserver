import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.kubernetes_rebuild_platform_adapter import (  # noqa: E402
    KubernetesRebuildPlatformAdapter,
)
from core.platform_adapter import (  # noqa: E402
    RebuildPlatformAdapterBuildRequest,
    build_rebuild_platform_adapter,
    normalize_platform_target,
)


class PlatformAdapterTests(unittest.TestCase):
    def _request(self, *, target: str) -> RebuildPlatformAdapterBuildRequest:
        return RebuildPlatformAdapterBuildRequest(
            target=target,
            environment_id="media-dev",
            namespace_service=mock.Mock(),
            manifest_apply_service=mock.Mock(),
            ingress_service=mock.Mock(),
            deployments_wait_service=mock.Mock(),
            smoke_test_service=mock.Mock(),
            info=mock.Mock(),
            run_kubectl=mock.Mock(),
        )

    def test_normalize_platform_target_maps_kubernetes_aliases(self):
        self.assertEqual(normalize_platform_target("k8s"), "k8s")
        self.assertEqual(normalize_platform_target("kubernetes"), "k8s")
        self.assertEqual(normalize_platform_target("microk8s"), "k8s")
        self.assertEqual(normalize_platform_target("compose"), "compose")
        self.assertEqual(normalize_platform_target("docker-compose"), "compose")

    def test_build_rebuild_platform_adapter_returns_k8s_adapter(self):
        adapter = build_rebuild_platform_adapter(self._request(target="kubernetes"))
        self.assertIsInstance(adapter, KubernetesRebuildPlatformAdapter)
        self.assertEqual(adapter.environment.target, "k8s")
        self.assertEqual(adapter.environment.environment_id, "media-dev")

    def test_build_rebuild_platform_adapter_returns_compose_adapter(self):
        adapter = build_rebuild_platform_adapter(self._request(target="compose"))
        self.assertEqual(adapter.environment.target, "compose")
        self.assertEqual(adapter.environment.environment_id, "media-dev")

    def test_build_rebuild_platform_adapter_rejects_unknown_target(self):
        with self.assertRaisesRegex(
            ValueError, "Unsupported rebuild platform target 'unsupported'"
        ):
            build_rebuild_platform_adapter(self._request(target="unsupported"))


if __name__ == "__main__":
    unittest.main()
